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

from .engine import PARTICIPATION_RATE, PaperEngine
from .models import (
    CommandReceipt,
    FifoLot,
    Journal,
    LedgerEntry,
    LotConsumption,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
)


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
    available_quantity: Decimal | None = None


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
    receipt: CommandReceiptWrite | None
    authorization_attempt: AuthorizationAttemptWrite | None
    broker_inputs: tuple[BrokerInputWrite, ...]
    balances: tuple[BalanceWrite, ...]
    order: PaperOrder | None
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


def _validate_outbox(item: OutboxWrite) -> None:
    payload = item.payload
    required = {
        "spec_version",
        "event_id",
        "event_type",
        "event_version",
        "occurred_at",
        "producer",
        "activation_phase",
        "aggregate_id",
        "aggregate_version",
        "payload_hash",
        "data",
    }
    data_fields = {
        "paper.order.accepted.v1": {"order_id"},
        "paper.order.partially-filled.v1": {"order_id", "fill_id"},
        "paper.order.filled.v1": {"order_id", "fill_id"},
        "paper.order.cancelled.v1": {"order_id", "cancel_id"},
        "paper.order.rejected.v1": {"request_hash", "reason"},
        "paper.authorization.attempted.v1": {"authorization_id", "outcome"},
        "ledger.transaction.posted.v1": {"transaction_id"},
    }
    if set(payload) != required or item.event_type not in data_fields:
        raise ValueError("INVALID_PAPER_OUTBOX_CONTRACT")
    data = payload["data"]
    if not isinstance(data, dict) or set(data) != data_fields[item.event_type]:
        raise ValueError("INVALID_PAPER_OUTBOX_CONTRACT")
    # Engine `_id` stringifies each item before JSON encoding.
    expected_hash = hashlib.sha256(
        json.dumps(
            [
                "event-payload",
                item.event_type,
                item.aggregate_id,
                str(item.aggregate_version),
                json.dumps(data, sort_keys=True, separators=(",", ":")),
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if (
        payload["spec_version"] != "woozoo.event/v1"
        or payload["event_id"] != item.event_id
        or payload["event_type"] != item.event_type
        or payload["event_version"] != 1
        or payload["occurred_at"] != item.occurred_at.isoformat()
        or payload["producer"] != "paper-engine"
        or payload["activation_phase"] != 7
        or payload["aggregate_id"] != item.aggregate_id
        or payload["aggregate_version"] != item.aggregate_version
        or payload["payload_hash"] != item.payload_hash
        or item.payload_hash != expected_hash
        or len(item.event_id) != 64
        or any(character not in "0123456789abcdef" for character in item.event_id)
        or len(item.aggregate_id) != 64
        or any(character not in "0123456789abcdef" for character in item.aggregate_id)
    ):
        raise ValueError("INVALID_PAPER_OUTBOX_CONTRACT")


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
            receipt = write.receipt
            if receipt is not None:
                prior = connection.execute(
                    "SELECT request_hash,response FROM paper_command_receipts "
                    "WHERE scope=%s AND idempotency_key=%s",
                    (receipt.scope, receipt.idempotency_key),
                ).fetchone()
                if prior is not None:
                    if prior[0] != receipt.request_hash:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    return CommitResult(
                        False,
                        prior[1],
                        self.semantic_digest(write.account_id, connection=connection),
                    )
            elif write.broker_inputs:
                prior_inputs = connection.execute(
                    "SELECT source_key,broker_seq,account_id,source_kind,payload_hash,"
                    "available_quantity FROM paper_broker_inputs WHERE source_key=ANY(%s)",
                    ([item.source_key for item in write.broker_inputs],),
                ).fetchall()
                if prior_inputs:
                    expected_inputs = {
                        (
                            item.source_key,
                            item.broker_seq,
                            item.account_id,
                            item.source_kind,
                            item.payload_hash,
                            item.available_quantity,
                        )
                        for item in write.broker_inputs
                    }
                    if set(prior_inputs) != expected_inputs:
                        raise ValueError("BROKER_INPUT_CONFLICT")
                    return CommitResult(
                        False,
                        {"status": "OBSERVATION_APPLIED"},
                        self.semantic_digest(write.account_id, connection=connection),
                    )
            else:
                raise ValueError("PAPER_WRITE_IDEMPOTENCY_SOURCE_REQUIRED")
            reconciliation = connection.execute(
                "SELECT status FROM paper_reconciliation_checkpoints WHERE account_id=%s "
                "ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
                (write.account_id,),
            ).fetchone()
            if reconciliation == ("FAILED",):
                raise RuntimeError("PAPER_RECONCILIATION_HOLD")

            connection.execute("SET CONSTRAINTS ALL DEFERRED")
            connection.execute(
                "INSERT INTO paper_accounts(account_id,namespace,created_at) VALUES (%s,%s,%s) "
                "ON CONFLICT (account_id) DO NOTHING",
                (write.account_id, write.namespace, write.created_at),
            )
            account = connection.execute(
                "SELECT namespace FROM paper_accounts WHERE account_id=%s",
                (write.account_id,),
            ).fetchone()
            if account != (write.namespace,):
                raise ValueError("ACCOUNT_NAMESPACE_CONFLICT")
            for item in write.broker_inputs:
                connection.execute(
                    "INSERT INTO paper_broker_inputs"
                    "(broker_seq,account_id,source_kind,source_key,payload_hash,observed_at,"
                    "available_quantity) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (source_key) DO NOTHING",
                    (
                        item.broker_seq,
                        item.account_id,
                        item.source_kind,
                        item.source_key,
                        item.payload_hash,
                        item.observed_at,
                        item.available_quantity,
                    ),
                )
                persisted_input = connection.execute(
                    "SELECT broker_seq,account_id,source_kind,payload_hash,available_quantity "
                    "FROM paper_broker_inputs WHERE source_key=%s",
                    (item.source_key,),
                ).fetchone()
                if persisted_input != (
                    item.broker_seq,
                    item.account_id,
                    item.source_kind,
                    item.payload_hash,
                    item.available_quantity,
                ):
                    raise ValueError("BROKER_INPUT_CONFLICT")
            if receipt is not None:
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
            if (receipt is None) != (attempt is None):
                raise ValueError("RECEIPT_ATTEMPT_PAIR_REQUIRED")
            if attempt is not None:
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
            if order is not None:
                if receipt is None:
                    existing_order = connection.execute(
                        "SELECT command_scope,idempotency_key FROM paper_orders "
                        "WHERE order_id=%s FOR UPDATE",
                        (order.order_id,),
                    ).fetchone()
                    if existing_order is None:
                        raise ValueError("ORDER_LIFECYCLE_REQUIRES_EXISTING_ORDER")
                    order_scope, order_key = existing_order
                elif receipt.outcome == "ORDER_CREATED":
                    order_scope, order_key = receipt.scope, receipt.idempotency_key
                else:
                    existing_order = connection.execute(
                        "SELECT command_scope,idempotency_key FROM paper_orders "
                        "WHERE order_id=%s FOR UPDATE",
                        (order.order_id,),
                    ).fetchone()
                    if existing_order is None:
                        raise ValueError("ORDER_LIFECYCLE_REQUIRES_EXISTING_ORDER")
                    order_scope, order_key = existing_order
                cursor = connection.execute(
                    "INSERT INTO paper_orders"
                    "(order_id,account_id,command_scope,idempotency_key,client_order_id,"
                    "authorization_id,symbol,side,order_type,time_in_force,quantity,limit_price,"
                    "filled_quantity,held_asset,held_amount,status,accepted_broker_seq,version) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (order_id) DO UPDATE SET "
                    "filled_quantity=EXCLUDED.filled_quantity,held_amount=EXCLUDED.held_amount,"
                    "status=EXCLUDED.status,version=EXCLUDED.version "
                    "WHERE paper_orders.version<EXCLUDED.version "
                    "AND paper_orders.account_id=EXCLUDED.account_id "
                    "AND paper_orders.client_order_id=EXCLUDED.client_order_id "
                    "AND paper_orders.authorization_id=EXCLUDED.authorization_id "
                    "AND paper_orders.symbol=EXCLUDED.symbol AND paper_orders.side=EXCLUDED.side "
                    "AND paper_orders.quantity=EXCLUDED.quantity "
                    "AND paper_orders.limit_price=EXCLUDED.limit_price",
                    (
                        order.order_id,
                        write.account_id,
                        order_scope,
                        order_key,
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
                if cursor.rowcount == 0:
                    current = connection.execute(
                        "SELECT filled_quantity,held_amount,status,version FROM paper_orders "
                        "WHERE order_id=%s",
                        (order.order_id,),
                    ).fetchone()
                    expected = (
                        order.filled_quantity,
                        order.held_amount,
                        order.status.value,
                        order.version,
                    )
                    if current != expected:
                        raise ValueError("ORDER_VERSION_CONFLICT")
            for balance in write.balances:
                cursor = connection.execute(
                    "INSERT INTO paper_asset_balances"
                    "(account_id,asset,available,held,version) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (account_id,asset) DO UPDATE SET "
                    "available=EXCLUDED.available,held=EXCLUDED.held,version=EXCLUDED.version "
                    "WHERE paper_asset_balances.version<EXCLUDED.version",
                    (
                        write.account_id,
                        balance.asset,
                        balance.available,
                        balance.held,
                        balance.version,
                    ),
                )
                if cursor.rowcount == 0:
                    current = connection.execute(
                        "SELECT available,held,version FROM paper_asset_balances "
                        "WHERE account_id=%s AND asset=%s",
                        (write.account_id, balance.asset),
                    ).fetchone()
                    if current != (balance.available, balance.held, balance.version):
                        raise ValueError("BALANCE_VERSION_CONFLICT")
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
                    "fee_amount,fee_policy_version,symbol_rule_version,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
                        fill.symbol_rule_version,
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
                _validate_outbox(outbox_event)
                connection.execute(
                    "SELECT append_paper_outbox(%s,%s,%s,%s,%s,%s,%s,%s)",
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
                connection.execute(
                    "INSERT INTO paper_outbox_links(event_id,account_id) VALUES (%s,%s)",
                    (outbox_event.event_id, write.account_id),
                )
            self._fail(PersistenceStage.OUTBOX, _fail_after)
        response: dict[str, object] = (
            receipt.response if receipt is not None else {"status": "OBSERVATION_APPLIED"}
        )
        return CommitResult(True, response, self.semantic_digest(write.account_id))

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
                "order_events": (
                    "SELECT event.event_id,event.order_id,event.order_version,event.event_type,"
                    "event.source_key,event.payload_hash FROM paper_order_events event JOIN "
                    "paper_orders paper_order USING(order_id) WHERE paper_order.account_id=%s "
                    "ORDER BY event.order_id,event.order_version"
                ),
                "fills": (
                    "SELECT fill.fill_id,fill.order_id,fill.source_key,fill.broker_seq,"
                    "fill.quantity,fill.price,fill.fee_asset,fill.fee_rate,fill.fee_amount,"
                    "fill.fee_policy_version,fill.symbol_rule_version FROM paper_fills fill "
                    "JOIN paper_orders paper_order USING(order_id) WHERE paper_order.account_id=%s "
                    "ORDER BY fill.fill_id"
                ),
                "lots": (
                    "SELECT lot_id,asset,acquired_quantity,quote_cost,source_fill_id "
                    "FROM paper_inventory_lots WHERE account_id=%s ORDER BY lot_id"
                ),
                "consumptions": (
                    "SELECT item.consumption_id,item.lot_id,item.source_fill_id,item.quantity,"
                    "item.quote_basis FROM paper_lot_consumptions item JOIN "
                    "paper_inventory_lots lot USING(lot_id) WHERE lot.account_id=%s "
                    "ORDER BY item.consumption_id"
                ),
                "ledger": (
                    "SELECT tx.transaction_id,e.line_no,e.account_code,e.commodity,"
                    "e.debit,e.credit FROM paper_ledger_transactions tx JOIN "
                    "paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "ORDER BY tx.transaction_id,e.line_no"
                ),
                "outbox": (
                    "SELECT event_id,event_type,aggregate_id,aggregate_version,payload_hash "
                    "FROM paper_outbox_events_v1 WHERE account_id=%s "
                    "ORDER BY event_id"
                ),
            }
            for key, query in queries.items():
                payload[key] = active.execute(query, (account_id,)).fetchall()
            return _digest(payload)
        finally:
            if own_connection:
                active.close()

    def hydrate_engine(self, account_id: str) -> PaperEngine:
        """Rebuild the deterministic aggregate from authoritative durable rows."""
        engine = PaperEngine()
        with psycopg.connect(self.database_url) as connection:
            if connection.execute(
                "SELECT count(*) FROM paper_accounts WHERE account_id=%s", (account_id,)
            ).fetchone() != (1,):
                raise KeyError("PAPER_ACCOUNT_NOT_FOUND")
            for asset, available, held in connection.execute(
                "SELECT asset,available,held FROM paper_asset_balances "
                "WHERE account_id=%s ORDER BY asset",
                (account_id,),
            ).fetchall():
                engine.available[asset] = available
                engine.held[asset] = held
            order_rows = connection.execute(
                "SELECT order_id,client_order_id,authorization_id,symbol,side,quantity,"
                "limit_price,accepted_broker_seq,status,filled_quantity,held_asset,held_amount,"
                "version FROM paper_orders WHERE account_id=%s ORDER BY order_id",
                (account_id,),
            ).fetchall()
            for row in order_rows:
                order = PaperOrder(
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    OrderSide(row[4]),
                    row[5],
                    row[6],
                    row[7],
                    OrderStatus(row[8]),
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                )
                engine.orders[order.order_id] = order
            for row in connection.execute(
                "SELECT fill.fill_id,fill.order_id,fill.source_key,fill.quantity,fill.price,"
                "fill.fee_asset,fill.fee_rate,fill.fee_amount,fill.broker_seq,"
                "fill.fee_policy_version,fill.symbol_rule_version FROM paper_fills fill "
                "JOIN paper_orders paper_order USING(order_id) WHERE paper_order.account_id=%s "
                "ORDER BY fill.broker_seq,fill.fill_id",
                (account_id,),
            ).fetchall():
                fill = PaperFill(*row)
                engine.fills[fill.fill_id] = fill
                engine.observation_effects.add((fill.order_id, fill.observation_id))
            engine.lots = tuple(
                FifoLot(*row)
                for row in connection.execute(
                    "SELECT lot_id,asset,acquired_quantity,quote_cost,source_fill_id,acquired_at "
                    "FROM paper_inventory_lots WHERE account_id=%s ORDER BY acquired_at,lot_id",
                    (account_id,),
                ).fetchall()
            )
            engine.consumptions = tuple(
                LotConsumption(*row)
                for row in connection.execute(
                    "SELECT item.consumption_id,item.lot_id,item.source_fill_id,item.quantity,"
                    "item.quote_basis FROM paper_lot_consumptions item JOIN "
                    "paper_inventory_lots lot USING(lot_id) WHERE lot.account_id=%s "
                    "ORDER BY item.consumption_id",
                    (account_id,),
                ).fetchall()
            )
            transaction_rows = connection.execute(
                "SELECT transaction_id,business_event_type,business_event_id,journal_kind,"
                "reversal_of,replacement_for FROM paper_ledger_transactions "
                "WHERE account_id=%s ORDER BY transaction_id",
                (account_id,),
            ).fetchall()
            for tx in transaction_rows:
                entries = tuple(
                    LedgerEntry(*row)
                    for row in connection.execute(
                        "SELECT account_code,commodity,debit,credit FROM paper_ledger_entries "
                        "WHERE transaction_id=%s ORDER BY line_no",
                        (tx[0],),
                    ).fetchall()
                )
                engine.journals[tx[0]] = Journal(tx[0], tx[1], tx[2], tx[3], entries, tx[4], tx[5])
            for scope, key, request_hash, outcome, order_id, response in connection.execute(
                "SELECT scope,idempotency_key,request_hash,outcome,paper_order_id,response "
                "FROM paper_command_receipts WHERE account_id=%s ORDER BY scope,idempotency_key",
                (account_id,),
            ).fetchall():
                if outcome == "REJECTED":
                    engine.command_receipts[key] = CommandReceipt(
                        request_hash, outcome, error_code=response["error_code"]
                    )
                elif outcome == "ORDER_CANCELLED":
                    assert order_id is not None
                    engine.cancel_receipts[key] = (order_id, engine.orders[order_id])
                    engine.order_cancel_identity[order_id] = key
                else:
                    engine.command_receipts[key] = CommandReceipt(request_hash, outcome, order_id)
            engine.authorization_attempts = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT authorization_id,outcome FROM paper_authorization_attempts "
                    "WHERE account_id=%s",
                    (account_id,),
                ).fetchall()
            }
            for source_key, payload_hash, available_quantity, allocated in connection.execute(
                "SELECT input.source_key,input.payload_hash,input.available_quantity,"
                "COALESCE(sum(fill.quantity),0) FROM paper_broker_inputs input LEFT JOIN "
                "paper_fills fill ON fill.source_key=input.source_key "
                "WHERE input.account_id=%s AND input.source_kind='RECORDED_BOOK' "
                "GROUP BY input.source_key,input.payload_hash,input.available_quantity",
                (account_id,),
            ).fetchall():
                engine.observation_budgets[source_key] = (
                    payload_hash,
                    available_quantity * PARTICIPATION_RATE - allocated,
                )
            engine.outbox = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT payload FROM paper_outbox_events_v1 WHERE account_id=%s "
                    "ORDER BY occurred_at,event_id",
                    (account_id,),
                ).fetchall()
            )
            max_sequence = connection.execute(
                "SELECT COALESCE(max(broker_seq),0) FROM paper_broker_inputs WHERE account_id=%s",
                (account_id,),
            ).fetchone()
            assert max_sequence is not None
            engine.broker_seq = max_sequence[0]
        return engine

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
            if not balance_rows and connection.execute(
                "SELECT EXISTS(SELECT 1 FROM paper_orders WHERE account_id=%s) OR "
                "EXISTS(SELECT 1 FROM paper_ledger_transactions WHERE account_id=%s)",
                (account_id, account_id),
            ).fetchone() == (True,):
                mismatches.append("INCOMPLETE_AUTHORITY_STATE")
            for asset, available, held in balance_rows:
                available_row = connection.execute(
                    "SELECT COALESCE(sum(e.debit-e.credit),0) FROM paper_ledger_transactions tx "
                    "JOIN paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "AND tx.journal_kind='PHYSICAL' AND e.commodity=%s "
                    "AND e.account_code IN ('paper.available','paper.asset')",
                    (
                        account_id,
                        asset,
                    ),
                ).fetchone()
                held_row = connection.execute(
                    "SELECT COALESCE(sum(e.debit-e.credit),0) FROM paper_ledger_transactions tx "
                    "JOIN paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "AND tx.journal_kind='PHYSICAL' AND e.commodity=%s "
                    "AND e.account_code='paper.held'",
                    (account_id, asset),
                ).fetchone()
                assert available_row is not None and held_row is not None
                ledger_available = available_row[0]
                ledger_held = held_row[0]
                if available != ledger_available or held != ledger_held:
                    mismatches.append("PHYSICAL_LEDGER_MISMATCH")
                    break
            ledger_assets = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT e.commodity FROM paper_ledger_transactions tx JOIN "
                    "paper_ledger_entries e USING(transaction_id) WHERE tx.account_id=%s "
                    "AND tx.journal_kind='PHYSICAL' AND e.account_code IN "
                    "('paper.available','paper.asset','paper.held')",
                    (account_id,),
                ).fetchall()
            }
            if ledger_assets != {row[0] for row in balance_rows}:
                mismatches.append("BALANCE_COMMODITY_COVERAGE_MISMATCH")
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
