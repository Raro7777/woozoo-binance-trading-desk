"""Internal Phase 4 PostgreSQL authority; exposes no command or network ingress."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json

import psycopg
from psycopg.types.json import Jsonb

from .models import FifoLot, Journal, LotConsumption, PaperFill, PaperOrder


class PersistenceStage(StrEnum):
    RECEIPT = "receipt"
    DOMAIN = "domain"
    LOT = "lot"
    LEDGER_HEADER = "ledger-header"
    LEDGER_ENTRY = "ledger-entry"
    OUTBOX = "outbox"


@dataclass(frozen=True, slots=True)
class CommandReceiptWrite:
    scope: str
    idempotency_key: str
    account_id: str
    request_hash: str
    outcome: str
    paper_order_id: str | None
    authorization_id: str
    broker_seq: int
    response: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuthorizationAttemptWrite:
    authorization_id: str
    authorization_nonce: str
    account_id: str
    command_scope: str
    idempotency_key: str
    namespace: str
    request_hash: str
    outcome: str
    reason_code: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerInputWrite:
    broker_seq: int
    account_id: str
    source_kind: str
    source_key: str
    payload_hash: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class BalanceWrite:
    asset: str
    available: Decimal
    held: Decimal
    version: int


@dataclass(frozen=True, slots=True)
class OrderEventWrite:
    event_id: str
    order_id: str
    order_version: int
    event_type: str
    source_key: str
    payload_hash: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxWrite:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload: dict[str, object]
    payload_hash: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AtomicPaperWrite:
    account_id: str
    namespace: str
    receipt: CommandReceiptWrite
    authorization_attempt: AuthorizationAttemptWrite
    broker_inputs: tuple[BrokerInputWrite, ...]
    balances: tuple[BalanceWrite, ...]
    order: PaperOrder
    order_events: tuple[OrderEventWrite, ...]
    fills: tuple[PaperFill, ...]
    lots: tuple[FifoLot, ...]
    consumptions: tuple[LotConsumption, ...]
    journals: tuple[Journal, ...]
    outbox: tuple[OutboxWrite, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CommitResult:
    created: bool
    response: dict[str, object]
    semantic_digest: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    mismatch_codes: tuple[str, ...]
    input_digest: str
    output_digest: str


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime)) or type(value).__name__ == "UUID":
        return str(value)
    raise TypeError(type(value).__name__)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class PostgresPaperStore:
    """Persists one already-authorized Paper effect in one SQL transaction."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _fail(stage: PersistenceStage, requested: PersistenceStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_PAPER_FAILURE:{stage.value}")

    def commit(
        self, write: AtomicPaperWrite, *, _fail_after: PersistenceStage | None = None
    ) -> CommitResult:
        with psycopg.connect(self.database_url) as connection:
            prior = connection.execute(
                "SELECT request_hash,response FROM paper_command_receipts "
                "WHERE scope=%s AND idempotency_key=%s",
                (write.receipt.scope, write.receipt.idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[0] != write.receipt.request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return CommitResult(
                    False, prior[1], self.semantic_digest(write.account_id, connection=connection)
                )

            connection.execute("SET CONSTRAINTS ALL DEFERRED")
            connection.execute(
                "INSERT INTO paper_accounts(account_id,namespace,created_at) VALUES (%s,%s,%s)",
                (write.account_id, write.namespace, write.created_at),
            )
            for item in write.broker_inputs:
                connection.execute(
                    "INSERT INTO paper_broker_inputs"
                    "(broker_seq,account_id,source_kind,source_key,payload_hash,observed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        item.broker_seq,
                        item.account_id,
                        item.source_kind,
                        item.source_key,
                        item.payload_hash,
                        item.observed_at,
                    ),
                )
            receipt = write.receipt
            connection.execute(
                "INSERT INTO paper_command_receipts"
                "(scope,idempotency_key,account_id,request_hash,outcome,paper_order_id,"
                "authorization_id,broker_seq,response,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    receipt.scope,
                    receipt.idempotency_key,
                    receipt.account_id,
                    receipt.request_hash,
                    receipt.outcome,
                    receipt.paper_order_id,
                    receipt.authorization_id,
                    receipt.broker_seq,
                    Jsonb(receipt.response),
                    receipt.created_at,
                ),
            )
            self._fail(PersistenceStage.RECEIPT, _fail_after)
            attempt = write.authorization_attempt
            connection.execute(
                "INSERT INTO paper_authorization_attempts"
                "(authorization_id,authorization_nonce,account_id,command_scope,"
                "idempotency_key,namespace,request_hash,outcome,reason_code,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    attempt.authorization_id,
                    attempt.authorization_nonce,
                    attempt.account_id,
                    attempt.command_scope,
                    attempt.idempotency_key,
                    attempt.namespace,
                    attempt.request_hash,
                    attempt.outcome,
                    attempt.reason_code,
                    attempt.created_at,
                ),
            )
            order = write.order
            connection.execute(
                "INSERT INTO paper_orders"
                "(order_id,account_id,command_scope,idempotency_key,client_order_id,"
                "authorization_id,symbol,side,order_type,time_in_force,quantity,limit_price,"
                "filled_quantity,held_asset,held_amount,status,accepted_broker_seq,version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    order.order_id,
                    write.account_id,
                    receipt.scope,
                    receipt.idempotency_key,
                    order.client_order_id,
                    order.authorization_id,
                    order.symbol,
                    order.side.value,
                    order.order_type,
                    order.time_in_force,
                    order.quantity,
                    order.limit_price,
                    order.filled_quantity,
                    order.held_asset,
                    order.held_amount,
                    order.status.value,
                    order.accepted_broker_seq,
                    order.version,
                ),
            )
            for balance in write.balances:
                connection.execute(
                    "INSERT INTO paper_asset_balances"
                    "(account_id,asset,available,held,version) VALUES (%s,%s,%s,%s,%s)",
                    (
                        write.account_id,
                        balance.asset,
                        balance.available,
                        balance.held,
                        balance.version,
                    ),
                )
            for event in write.order_events:
                connection.execute(
                    "INSERT INTO paper_order_events"
                    "(event_id,order_id,order_version,event_type,source_key,payload_hash,occurred_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        event.event_id,
                        event.order_id,
                        event.order_version,
                        event.event_type,
                        event.source_key,
                        event.payload_hash,
                        event.occurred_at,
                    ),
                )
            for fill in write.fills:
                connection.execute(
                    "INSERT INTO paper_fills"
                    "(fill_id,order_id,source_key,broker_seq,quantity,price,fee_asset,fee_rate,"
                    "fee_amount,fee_policy_version,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        fill.fill_id,
                        fill.order_id,
                        fill.observation_id,
                        fill.broker_seq,
                        fill.quantity,
                        fill.price,
                        fill.fee_asset,
                        fill.fee_rate,
                        fill.fee_amount,
                        fill.fee_policy_version,
                        write.created_at,
                    ),
                )
            self._fail(PersistenceStage.DOMAIN, _fail_after)
            for lot in write.lots:
                connection.execute(
                    "INSERT INTO paper_inventory_lots"
                    "(lot_id,account_id,asset,acquired_quantity,quote_cost,source_fill_id,acquired_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        lot.lot_id,
                        write.account_id,
                        lot.asset,
                        lot.acquired_quantity,
                        lot.quote_cost,
                        lot.source_fill_id,
                        lot.acquired_at,
                    ),
                )
            for consumption in write.consumptions:
                connection.execute(
                    "INSERT INTO paper_lot_consumptions"
                    "(consumption_id,lot_id,source_fill_id,quantity,quote_basis) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        consumption.consumption_id,
                        consumption.lot_id,
                        consumption.source_fill_id,
                        consumption.quantity,
                        consumption.quote_basis,
                    ),
                )
            self._fail(PersistenceStage.LOT, _fail_after)
            for journal in write.journals:
                connection.execute(
                    "INSERT INTO paper_ledger_transactions"
                    "(transaction_id,account_id,business_event_type,business_event_id,"
                    "journal_kind,reversal_of,replacement_for,posted_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        journal.journal_id,
                        write.account_id,
                        journal.business_event_type,
                        journal.business_event_id,
                        journal.journal_kind,
                        journal.reversal_of,
                        journal.replacement_for,
                        write.created_at,
                    ),
                )
            self._fail(PersistenceStage.LEDGER_HEADER, _fail_after)
            for journal in write.journals:
                for line_no, entry in enumerate(journal.entries):
                    connection.execute(
                        "INSERT INTO paper_ledger_entries"
                        "(transaction_id,line_no,account_code,commodity,debit,credit) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (
                            journal.journal_id,
                            line_no,
                            entry.account,
                            entry.commodity,
                            entry.debit,
                            entry.credit,
                        ),
                    )
            self._fail(PersistenceStage.LEDGER_ENTRY, _fail_after)
            for outbox_event in write.outbox:
                connection.execute(
                    "INSERT INTO outbox_events"
                    "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
                    "aggregate_id,aggregate_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        outbox_event.event_id,
                        outbox_event.event_type,
                        Jsonb(outbox_event.payload),
                        outbox_event.payload_hash,
                        outbox_event.occurred_at,
                        outbox_event.aggregate_type,
                        outbox_event.aggregate_id,
                        outbox_event.aggregate_version,
                    ),
                )
            self._fail(PersistenceStage.OUTBOX, _fail_after)
        return CommitResult(True, receipt.response, self.semantic_digest(write.account_id))

    def semantic_digest(
        self, account_id: str, *, connection: psycopg.Connection[object] | None = None
    ) -> str:
        own_connection = connection is None
        active = connection or psycopg.connect(self.database_url)
        try:
            payload: dict[str, object] = {}
            queries = {
                "receipt": (
                    "SELECT scope,idempotency_key,request_hash,outcome,paper_order_id,"
                    "authorization_id,broker_seq,response FROM paper_command_receipts "
                    "WHERE account_id=%s ORDER BY scope,idempotency_key"
                ),
                "attempt": (
                    "SELECT authorization_id,authorization_nonce,command_scope,"
                    "idempotency_key,request_hash,outcome,reason_code "
                    "FROM paper_authorization_attempts WHERE account_id=%s "
                    "ORDER BY authorization_id"
                ),
                "input": (
                    "SELECT broker_seq,source_kind,source_key,payload_hash FROM "
                    "paper_broker_inputs WHERE account_id=%s ORDER BY broker_seq"
                ),
                "balance": (
                    "SELECT asset,available,held,version FROM paper_asset_balances "
                    "WHERE account_id=%s ORDER BY asset"
                ),
                "orders": (
                    "SELECT order_id,filled_quantity,held_amount,status,version FROM "
                    "paper_orders WHERE account_id=%s ORDER BY order_id"
                ),
                "ledger": (
                    "SELECT tx.transaction_id,e.line_no,e.account_code,e.commodity,"
                    "e.debit,e.credit FROM paper_ledger_transactions tx JOIN "
                    "paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "ORDER BY tx.transaction_id,e.line_no"
                ),
                "outbox": (
                    "SELECT event_id,event_type,aggregate_id,aggregate_version,payload_hash "
                    "FROM outbox_events WHERE aggregate_id IN "
                    "(SELECT order_id FROM paper_orders WHERE account_id=%s) "
                    "ORDER BY event_id"
                ),
            }
            for key, query in queries.items():
                payload[key] = active.execute(query, (account_id,)).fetchall()
            return _digest(payload)
        finally:
            if own_connection:
                active.close()

    def reconcile(
        self, account_id: str, *, checkpoint_id: str, created_at: datetime
    ) -> ReconciliationResult:
        with psycopg.connect(self.database_url) as connection:
            state_digest = self.semantic_digest(account_id, connection=connection)
            balance_rows = connection.execute(
                "SELECT asset,available,held FROM paper_asset_balances "
                "WHERE account_id=%s ORDER BY asset",
                (account_id,),
            ).fetchall()
            mismatches: list[str] = []
            for asset, available, held in balance_rows:
                available_row = connection.execute(
                    "SELECT COALESCE(sum(e.debit-e.credit),0) FROM paper_ledger_transactions tx "
                    "JOIN paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "AND e.commodity=%s AND e.account_code=%s",
                    (
                        account_id,
                        asset,
                        "paper.asset" if asset in {"BTC", "ETH"} else "paper.available",
                    ),
                ).fetchone()
                held_row = connection.execute(
                    "SELECT COALESCE(sum(e.debit-e.credit),0) FROM paper_ledger_transactions tx "
                    "JOIN paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "AND e.commodity=%s AND e.account_code='paper.held'",
                    (account_id, asset),
                ).fetchone()
                assert available_row is not None and held_row is not None
                ledger_available = available_row[0]
                ledger_held = held_row[0]
                if available != ledger_available or held != ledger_held:
                    mismatches.append("PHYSICAL_LEDGER_MISMATCH")
                    break
            status = "FAILED" if mismatches else "HEALTHY"
            output_digest = _digest({"input": state_digest, "mismatches": mismatches})
            connection.execute(
                "INSERT INTO paper_reconciliation_checkpoints"
                "(checkpoint_id,account_id,input_digest,output_digest,status,mismatch_codes,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    checkpoint_id,
                    account_id,
                    state_digest,
                    output_digest,
                    status,
                    Jsonb(mismatches),
                    created_at,
                ),
            )
        return ReconciliationResult(status, tuple(mismatches), state_digest, output_digest)
