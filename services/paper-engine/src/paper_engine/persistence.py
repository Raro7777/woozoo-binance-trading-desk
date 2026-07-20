"""Internal Phase 4 PostgreSQL authority; exposes no command or network ingress."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal
from enum import StrEnum
import hashlib
import json
from collections.abc import Callable
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from .decimal_policy import add, canonical, decimal_input, floor_product_to_step, multiply, subtract
from .engine import FEE_RATE, PARTICIPATION_RATE, SYMBOL_RULES, PaperEngine
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


class KillCancelStage(StrEnum):
    INBOX = "inbox"
    BATCH = "batch"
    DOMAIN = "domain"
    LEDGER = "ledger"
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
    symbol: str | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None


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
    kill_switch_version: int = 0


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


@dataclass(frozen=True, slots=True)
class KillCancelResult:
    inbox_created: bool
    batch_created: bool
    activation_event_id: str
    batch_key: str | None
    account_id: str | None
    cancelled_count: int
    has_more: bool
    completion_created: bool = False


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime)) or type(value).__name__ == "UUID":
        return str(value)
    raise TypeError(type(value).__name__)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _engine_id(kind: str, *parts: object) -> str:
    raw = json.dumps([kind, *[str(part) for part in parts]], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def kill_cancel_id(activation_event_id: str, order_id: str) -> str:
    """Bind the complete Kill activation and Paper order identities."""
    return _digest(
        {
            "schema_version": "woozoo.paper-kill-cancel-id/v1",
            "activation_event_id": activation_event_id,
            "order_id": order_id,
        }
    )


PAPER_DEFAULT_ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"


def _validate_broker_input(item: BrokerInputWrite) -> None:
    observation_fields = (item.symbol, item.best_bid, item.best_ask, item.available_quantity)
    if item.source_kind == "TEST_COMMAND":
        if any(field is not None for field in observation_fields):
            raise ValueError("INVALID_TEST_COMMAND_INPUT")
        return
    if item.source_kind == "PAPER_AUTHORIZATION":
        if any(field is not None for field in observation_fields):
            raise ValueError("INVALID_PAPER_AUTHORIZATION_INPUT")
        return
    if item.source_kind != "RECORDED_BOOK" or any(field is None for field in observation_fields):
        raise ValueError("INVALID_RECORDED_BOOK_INPUT")
    assert item.symbol is not None
    assert item.best_bid is not None and item.best_ask is not None
    assert item.available_quantity is not None
    expected = _engine_id(
        "observation",
        item.symbol,
        canonical(item.best_bid),
        canonical(item.best_ask),
        canonical(item.available_quantity),
    )
    if item.symbol not in SYMBOL_RULES or item.payload_hash != expected:
        raise ValueError("RECORDED_BOOK_HASH_CONFLICT")


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
    aggregate_types = {
        "paper.order.accepted.v1": "paper_order",
        "paper.order.partially-filled.v1": "paper_order",
        "paper.order.filled.v1": "paper_order",
        "paper.order.cancelled.v1": "paper_order",
        "paper.order.rejected.v1": "paper_request",
        "paper.authorization.attempted.v1": "paper_authorization",
        "ledger.transaction.posted.v1": "paper_ledger",
    }
    if set(payload) != required or item.event_type not in data_fields:
        raise ValueError("INVALID_PAPER_OUTBOX_CONTRACT")
    data = payload["data"]
    if (
        not isinstance(data, dict)
        or set(data) != data_fields[item.event_type]
        or any(not isinstance(value, str) for value in data.values())
    ):
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
    expected_event_id = _engine_id(
        "event", item.event_type, item.aggregate_id, item.aggregate_version
    )
    hexadecimal_fields = {
        "order_id",
        "fill_id",
        "request_hash",
        "transaction_id",
    }
    if any(
        len(data[field]) != 64
        or any(character not in "0123456789abcdef" for character in data[field])
        for field in hexadecimal_fields.intersection(data)
    ):
        raise ValueError("INVALID_PAPER_OUTBOX_CONTRACT")
    if item.event_type.startswith("paper.order.") and item.event_type != "paper.order.rejected.v1":
        linked = data["order_id"] == item.aggregate_id
    elif item.event_type == "paper.order.rejected.v1":
        linked = (
            data["request_hash"] == item.aggregate_id and data["reason"] == "INSUFFICIENT_FUNDS"
        )
    elif item.event_type == "paper.authorization.attempted.v1":
        linked = (
            data["outcome"] in {"BLOCKED", "CONSUMED"}
            and bool(data["authorization_id"])
            and item.aggregate_id == _engine_id("authorization", data["authorization_id"])
        )
    else:
        linked = data["transaction_id"] == item.aggregate_id
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
        or item.aggregate_type != aggregate_types[item.event_type]
        or item.event_id != expected_event_id
        or item.aggregate_version < 1
        or not linked
        or len(item.event_id) != 64
        or any(character not in "0123456789abcdef" for character in item.event_id)
        or len(item.aggregate_id) != 64
        or any(character not in "0123456789abcdef" for character in item.aggregate_id)
    ):
        raise ValueError("INVALID_PAPER_OUTBOX_CONTRACT")


def _paper_outbox(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    data: dict[str, str],
    occurred_at: datetime,
) -> OutboxWrite:
    event_id = _engine_id("event", event_type, aggregate_id, aggregate_version)
    payload_hash = _engine_id(
        "event-payload",
        event_type,
        aggregate_id,
        aggregate_version,
        json.dumps(data, sort_keys=True, separators=(",", ":")),
    )
    payload: dict[str, object] = {
        "spec_version": "woozoo.event/v1",
        "event_id": event_id,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "producer": "paper-engine",
        "activation_phase": 7,
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "payload_hash": payload_hash,
        "data": data,
    }
    return OutboxWrite(
        event_id,
        event_type,
        aggregate_type,
        aggregate_id,
        aggregate_version,
        payload,
        payload_hash,
        occurred_at,
    )


def _phase7_paper_outbox(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    data: dict[str, object],
    occurred_at: datetime,
) -> OutboxWrite:
    """Build the production-only Phase 7 Paper event envelope."""
    if event_type not in {
        "paper.authorization.blocked.v2",
        "paper.authorization.consumed.v2",
        "paper.order.accepted.v2",
        "paper.order.cancelled.v2",
        "ledger.transaction.posted.v2",
    }:
        raise ValueError("INVALID_PHASE7_PAPER_EVENT_TYPE")
    event_id = _engine_id("event", event_type, aggregate_id, aggregate_version)
    payload_hash = _digest(data)
    payload: dict[str, object] = {
        "spec_version": "woozoo.event/v1",
        "event_id": event_id,
        "event_type": event_type,
        "event_version": 2,
        "occurred_at": occurred_at.isoformat(),
        "producer": "paper-engine",
        "activation_phase": 7,
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "payload_hash": payload_hash,
        "data": data,
    }
    return OutboxWrite(
        event_id,
        event_type,
        aggregate_type,
        aggregate_id,
        aggregate_version,
        payload,
        payload_hash,
        occurred_at,
    )


class Phase7AuthorizationWorker:
    """One-shot consumer for durable production authorization issuance events."""

    def __init__(self, store: PostgresPaperStore) -> None:
        self._store = store

    def run_once(self) -> CommitResult | None:
        with psycopg.connect(self._store.database_url) as connection:
            pending = connection.execute(
                "SELECT authorization_id FROM paper_pending_authorizations_v1 "
                "ORDER BY issued_at,authorization_id LIMIT 1"
            ).fetchone()
        if pending is None:
            return None
        return self._store.attempt_phase7_authorization(pending[0])


class PostgresPaperStore:
    """Persists one already-authorized Paper effect in one SQL transaction."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _append_outbox(
        connection: psycopg.Connection[object], account_id: str, item: OutboxWrite
    ) -> None:
        _validate_outbox(item)
        connection.execute(
            "SELECT append_paper_outbox(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                item.event_id,
                item.event_type,
                Jsonb(item.payload),
                item.payload_hash,
                item.occurred_at,
                item.aggregate_type,
                item.aggregate_id,
                item.aggregate_version,
            ),
        )
        connection.execute(
            "INSERT INTO paper_outbox_links(event_id,account_id) VALUES (%s,%s)",
            (item.event_id, account_id),
        )

    @staticmethod
    def _append_phase7_outbox(
        connection: psycopg.Connection[object], account_id: str, item: OutboxWrite
    ) -> None:
        if item.payload.get("event_version") != 2:
            raise ValueError("PHASE7_PAPER_EVENT_VERSION_REQUIRED")
        connection.execute(
            "SELECT append_paper_outbox(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                item.event_id,
                item.event_type,
                Jsonb(item.payload),
                item.payload_hash,
                item.occurred_at,
                item.aggregate_type,
                item.aggregate_id,
                item.aggregate_version,
            ),
        )
        connection.execute(
            "INSERT INTO paper_outbox_links(event_id,account_id) VALUES (%s,%s)",
            (item.event_id, account_id),
        )

    def attempt_phase7_authorization(self, authorization_id: str) -> CommitResult:
        """Attempt one production Paper authorization using DB-owned financial inputs."""
        with psycopg.connect(self.database_url) as connection:
            identity = connection.execute(
                "SELECT approval_id,paper_account_id FROM paper_execution_authorizations "
                "WHERE authorization_id=%s AND namespace='paper'",
                (authorization_id,),
            ).fetchone()
            if identity is None:
                raise KeyError("PAPER_AUTHORIZATION_NOT_FOUND")
            approval_id, account_id = identity
            for lock_key in (
                f"paper-approval:{approval_id}",
                f"paper-authorization:{authorization_id}",
                f"paper-account:{account_id}",
                f"command:CREATE_ORDER:{authorization_id}",
            ):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,)
                )
            authorization = connection.execute(
                "SELECT * FROM paper_lock_execution_authorization_v1(%s)",
                (authorization_id,),
            ).fetchone()
            if authorization is None:
                raise RuntimeError("PAPER_AUTHORIZATION_DISAPPEARED")
            (
                authorization_nonce,
                account_id,
                authorization_digest,
                expected_kill_version,
                expected_reconciliation_hash,
                expected_ledger_hash,
                expires_at,
                approval_id,
                proposal_hash,
                risk_decision_hash,
                preview,
                preview_hash,
                expected_data_hash,
                expected_data_as_of,
                expected_knowledge_cutoff,
                expected_books,
            ) = authorization
            revoked = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM paper_approval_revocations WHERE approval_id=%s)",
                (approval_id,),
            ).fetchone()
            assert revoked is not None
            request_hash = _digest(
                {
                    "authorization_id": authorization_id,
                    "authorization_input_digest": authorization_digest,
                    "operation": "PAPER_FIRST_ATTEMPT",
                }
            )
            if not isinstance(preview, dict):
                raise ValueError("INVALID_PAPER_ORDER_PREVIEW")
            prior = connection.execute(
                "SELECT request_hash,response FROM paper_command_receipts "
                "WHERE scope='CREATE_ORDER' AND idempotency_key=%s",
                (authorization_id,),
            ).fetchone()
            if prior is not None:
                if prior[0] != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return CommitResult(
                    False,
                    prior[1],
                    self.semantic_digest(account_id, connection=connection),
                )

            account = connection.execute(
                "SELECT namespace FROM paper_accounts WHERE account_id=%s",
                (account_id,),
            ).fetchone()
            if account != ("paper",):
                raise ValueError("PRODUCTION_PAPER_ACCOUNT_REQUIRED")
            barrier = connection.execute(
                "SELECT active,version FROM paper_lock_kill_barrier()"
            ).fetchone()
            latest_reconciliation = connection.execute(
                "SELECT checkpoint_id,status,mismatch_codes FROM "
                "paper_reconciliation_checkpoints WHERE account_id=%s "
                "ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
                (account_id,),
            ).fetchone()
            current_ledger_hash = self.semantic_digest(account_id, connection=connection)
            database_now = connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()
            assert database_now is not None
            current_time = database_now[0]

            data_block_reason: str | None = None
            symbol = preview.get("symbol")
            if data_block_reason is None and symbol not in SYMBOL_RULES:
                data_block_reason = "DATA_INVALID"
            if not isinstance(expected_books, dict):
                data_block_reason = "HASH_MISMATCH"
            evidence = None
            if data_block_reason is None:
                evidence = connection.execute(
                    "SELECT evidence_id,evidence_digest,as_of,knowledge_cutoff,quality_status "
                    "FROM evidence_snapshots WHERE symbol=%s "
                    "ORDER BY as_of DESC,knowledge_cutoff DESC,evidence_id DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
                if evidence is None or evidence[4] != "healthy":
                    data_block_reason = "DATA_INVALID"
                elif (
                    evidence[2] > current_time
                    or evidence[3] > current_time
                    or current_time - evidence[2] > timedelta(minutes=5)
                    or current_time - evidence[3] > timedelta(minutes=5)
                ):
                    data_block_reason = "DATA_STALE"
                elif (
                    evidence[2] != expected_data_as_of
                    or evidence[3] != expected_knowledge_cutoff
                    or _digest(
                        {
                            "evidence_id": evidence[0],
                            "evidence_hash": evidence[1],
                            "as_of": evidence[2].isoformat().replace("+00:00", "Z"),
                            "knowledge_cutoff": evidence[3].isoformat().replace("+00:00", "Z"),
                            "freshness": "FRESH",
                            "quality": "HEALTHY",
                            "future_contamination": False,
                            "watermark_complete": True,
                        }
                    )
                    != expected_data_hash
                ):
                    data_block_reason = "HASH_MISMATCH"

            books: dict[str, tuple[object, ...]] = {}
            if data_block_reason is None:
                for row in connection.execute(
                    "SELECT DISTINCT ON (symbol) symbol,payload,event_time,received_at,"
                    "quality_status FROM normalized_market_events "
                    "WHERE event_type='book_ticker' AND symbol IN ('BTCUSDT','ETHUSDT') "
                    "ORDER BY symbol,event_time DESC,received_at DESC,id DESC"
                ).fetchall():
                    books[str(row[0])] = cast(tuple[object, ...], row)
                if set(books) != {"BTCUSDT", "ETHUSDT"}:
                    data_block_reason = "DATA_INVALID"
                elif any(row[4] != "healthy" for row in books.values()):
                    data_block_reason = "DATA_INVALID"
                elif any(
                    row[2] > current_time
                    or row[3] > current_time
                    or current_time - row[2] > timedelta(seconds=5)
                    or current_time - row[3] > timedelta(seconds=5)
                    for row in books.values()
                ):
                    data_block_reason = "DATA_STALE"
                else:
                    parsed_books: dict[str, tuple[Decimal, Decimal]] = {}
                    for current_symbol, current_book in books.items():
                        payload = current_book[1]
                        if not isinstance(payload, dict):
                            data_block_reason = "HASH_MISMATCH"
                            break
                        current_bid = decimal_input(str(payload.get("bid_price")))
                        current_ask = decimal_input(str(payload.get("ask_price")))
                        if current_bid <= 0 or current_ask <= 0 or current_bid > current_ask:
                            data_block_reason = "DATA_INVALID"
                            break
                        parsed_books[current_symbol] = (current_bid, current_ask)
                    if data_block_reason is None:
                        assert isinstance(expected_books, dict)
                        for position_symbol, (current_bid, current_ask) in parsed_books.items():
                            expected_book = expected_books.get(position_symbol)
                            if (
                                not isinstance(expected_book, dict)
                                or current_bid != decimal_input(str(expected_book.get("best_bid")))
                                or current_ask != decimal_input(str(expected_book.get("best_ask")))
                            ):
                                data_block_reason = "HASH_MISMATCH"
                                break
                    if data_block_reason is None:
                        target_bid, target_ask = parsed_books[str(symbol)]
                        if target_bid != decimal_input(
                            str(preview.get("best_bid"))
                        ) or target_ask != decimal_input(str(preview.get("best_ask"))):
                            data_block_reason = "HASH_MISMATCH"

            block_reason: str | None = None
            if barrier is None or barrier[0]:
                block_reason = "KILL_SWITCH_ACTIVE"
            elif barrier[1] != expected_kill_version:
                block_reason = "KILL_VERSION_MISMATCH"
            elif database_now[0] >= expires_at:
                block_reason = "AUTHORIZATION_EXPIRED"
            elif revoked[0]:
                block_reason = "AUTHORIZATION_REVOKED"
            elif data_block_reason is not None:
                block_reason = data_block_reason
            elif latest_reconciliation is None:
                block_reason = "RECONCILIATION_MISSING"
            else:
                checkpoint_id, health, mismatch_codes = latest_reconciliation
                reconciliation_hash = _digest(
                    {
                        "checkpoint_id": checkpoint_id,
                        "health": health,
                        "mismatch_codes": mismatch_codes,
                    }
                )
                if health != "HEALTHY" or reconciliation_hash != expected_reconciliation_hash:
                    block_reason = "RECONCILIATION_UNHEALTHY"
            if block_reason is None and current_ledger_hash != expected_ledger_hash:
                block_reason = "LEDGER_UNHEALTHY"

            broker_seq_row = connection.execute(
                "SELECT COALESCE(max(broker_seq),0)+1 FROM paper_broker_inputs WHERE account_id=%s",
                (account_id,),
            ).fetchone()
            assert broker_seq_row is not None
            broker_seq = broker_seq_row[0]
            occurred_at = database_now[0].astimezone(UTC)
            connection.execute("SET CONSTRAINTS ALL DEFERRED")
            connection.execute(
                "INSERT INTO paper_broker_inputs"
                "(broker_seq,account_id,source_kind,source_key,payload_hash,observed_at,"
                "paper_execution_authorization_id) "
                "VALUES (%s,%s,'PAPER_AUTHORIZATION',%s,%s,%s,%s)",
                (
                    broker_seq,
                    account_id,
                    authorization_id,
                    authorization_digest,
                    occurred_at,
                    authorization_id,
                ),
            )

            if block_reason is not None:
                response: dict[str, object] = {
                    "result": "BLOCKED",
                    "authorization_id": authorization_id,
                    "reason_code": block_reason,
                }
                self._insert_phase7_receipt_attempt(
                    connection,
                    authorization_id=authorization_id,
                    authorization_nonce=authorization_nonce,
                    account_id=account_id,
                    request_hash=request_hash,
                    broker_seq=broker_seq,
                    response=response,
                    occurred_at=occurred_at,
                    outcome="REJECTED",
                    attempt_outcome="BLOCKED",
                    reason_code=block_reason,
                    order_id=None,
                )
                blocked = _phase7_paper_outbox(
                    "paper.authorization.blocked.v2",
                    "paper_authorization",
                    authorization_id,
                    1,
                    {
                        "authorization_id": authorization_id,
                        "authorization_nonce": authorization_nonce,
                        "approval_id": approval_id,
                        "request_hash": request_hash,
                        "outcome": "BLOCKED",
                        "reason_code": block_reason,
                    },
                    occurred_at,
                )
                self._append_phase7_outbox(connection, account_id, blocked)
                semantic_digest = self.semantic_digest(account_id, connection=connection)
                return CommitResult(True, response, semantic_digest)

            return self._create_phase7_order(
                connection,
                authorization_id=authorization_id,
                authorization_nonce=authorization_nonce,
                approval_id=approval_id,
                proposal_hash=proposal_hash,
                risk_decision_hash=risk_decision_hash,
                account_id=account_id,
                request_hash=request_hash,
                broker_seq=broker_seq,
                preview=preview,
                preview_hash=preview_hash,
                occurred_at=occurred_at,
            )

    @staticmethod
    def _insert_phase7_receipt_attempt(
        connection: psycopg.Connection[object],
        *,
        authorization_id: str,
        authorization_nonce: str,
        account_id: str,
        request_hash: str,
        broker_seq: int,
        response: dict[str, object],
        occurred_at: datetime,
        outcome: str,
        attempt_outcome: str,
        reason_code: str | None,
        order_id: str | None,
    ) -> None:
        connection.execute(
            "INSERT INTO paper_command_receipts"
            "(scope,idempotency_key,account_id,request_hash,outcome,paper_order_id,"
            "authorization_id,broker_seq,response,created_at) "
            "VALUES ('CREATE_ORDER',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                authorization_id,
                account_id,
                request_hash,
                outcome,
                order_id,
                authorization_id,
                broker_seq,
                Jsonb(response),
                occurred_at,
            ),
        )
        connection.execute(
            "INSERT INTO paper_authorization_attempts"
            "(authorization_id,authorization_nonce,account_id,command_scope,"
            "idempotency_key,namespace,request_hash,outcome,reason_code,created_at,"
            "paper_execution_authorization_id) "
            "VALUES (%s,%s,%s,'CREATE_ORDER',%s,'paper',%s,%s,%s,%s,%s)",
            (
                authorization_id,
                authorization_nonce,
                account_id,
                authorization_id,
                request_hash,
                attempt_outcome,
                reason_code,
                occurred_at,
                authorization_id,
            ),
        )

    def _create_phase7_order(
        self,
        connection: psycopg.Connection[object],
        *,
        authorization_id: str,
        authorization_nonce: str,
        approval_id: str,
        proposal_hash: str,
        risk_decision_hash: str,
        account_id: str,
        request_hash: str,
        broker_seq: int,
        preview: dict[str, object],
        preview_hash: str,
        occurred_at: datetime,
    ) -> CommitResult:
        preview_without_hash = {
            key: value for key, value in preview.items() if key != "paper_order_preview_hash"
        }
        if (
            preview.get("paper_order_preview_hash") != preview_hash
            or _digest(preview_without_hash) != preview_hash
        ):
            raise ValueError("PAPER_PREVIEW_HASH_MISMATCH")
        symbol = preview.get("symbol")
        side_text = preview.get("side")
        if (
            symbol not in SYMBOL_RULES
            or side_text not in {"BUY", "SELL"}
            or preview.get("order_type") != "LIMIT"
            or preview.get("time_in_force") != "GTC"
        ):
            raise ValueError("INVALID_PAPER_ORDER_PREVIEW")
        quantity = decimal_input(str(preview.get("quantity")), positive=True)
        limit_price = decimal_input(str(preview.get("limit_price")), positive=True)
        rule = SYMBOL_RULES[str(symbol)]
        if quantity < rule["min_quantity"] or quantity % rule["step"] != 0:
            raise ValueError("QUANTITY_FILTER_FAILED")
        if limit_price % rule["tick"] != 0:
            raise ValueError("PRICE_FILTER_FAILED")
        principal = multiply(quantity, limit_price, rounding=ROUND_UP)
        if principal < rule["min_notional"]:
            raise ValueError("NOTIONAL_FILTER_FAILED")
        held_asset = "USDT" if side_text == "BUY" else str(symbol).removesuffix("USDT")
        held_amount = (
            add(
                principal,
                multiply(principal, FEE_RATE, rounding=ROUND_UP),
                rounding=ROUND_UP,
            )
            if side_text == "BUY"
            else quantity
        )
        if decimal_input(str(preview.get("worst_case_hold"))) != held_amount:
            raise ValueError("PAPER_PREVIEW_HOLD_MISMATCH")

        balance = cast(
            tuple[Decimal, Decimal, int] | None,
            connection.execute(
                "SELECT available,held,version FROM paper_asset_balances "
                "WHERE account_id=%s AND asset=%s FOR UPDATE",
                (account_id, held_asset),
            ).fetchone(),
        )
        if balance is None:
            raise RuntimeError("PAPER_BALANCE_MISSING")
        available, held, balance_version = balance
        if available < held_amount:
            reason_code = "INSUFFICIENT_FUNDS"
            response: dict[str, object] = {
                "result": "BLOCKED",
                "authorization_id": authorization_id,
                "reason_code": reason_code,
            }
            self._insert_phase7_receipt_attempt(
                connection,
                authorization_id=authorization_id,
                authorization_nonce=authorization_nonce,
                account_id=account_id,
                request_hash=request_hash,
                broker_seq=broker_seq,
                response=response,
                occurred_at=occurred_at,
                outcome="REJECTED",
                attempt_outcome="BLOCKED",
                reason_code=reason_code,
                order_id=None,
            )
            blocked = _phase7_paper_outbox(
                "paper.authorization.blocked.v2",
                "paper_authorization",
                authorization_id,
                1,
                {
                    "authorization_id": authorization_id,
                    "authorization_nonce": authorization_nonce,
                    "approval_id": approval_id,
                    "request_hash": request_hash,
                    "outcome": "BLOCKED",
                    "reason_code": reason_code,
                },
                occurred_at,
            )
            self._append_phase7_outbox(connection, account_id, blocked)
            return CommitResult(
                True,
                response,
                self.semantic_digest(account_id, connection=connection),
            )

        client_order_id = _engine_id("client-order", authorization_id)
        order_id = _engine_id("order", client_order_id)
        journal_id = _engine_id("hold-journal", order_id)
        order_data: dict[str, object] = {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "authorization_id": authorization_id,
            "authorization_namespace": "paper",
            "authorization_nonce": authorization_nonce,
            "approval_id": approval_id,
            "proposal_hash": proposal_hash,
            "risk_decision_hash": risk_decision_hash,
            "paper_order_preview_hash": preview_hash,
            "symbol": symbol,
            "side": side_text,
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "quantity": canonical(quantity),
            "limit_price": canonical(limit_price),
            "filled_quantity": canonical(Decimal(0)),
            "status": "OPEN",
            "version": 1,
        }
        response = {
            "result": "CONSUMED_ORDER_CREATED",
            "authorization_id": authorization_id,
            "order_id": order_id,
            "status": "OPEN",
        }
        self._insert_phase7_receipt_attempt(
            connection,
            authorization_id=authorization_id,
            authorization_nonce=authorization_nonce,
            account_id=account_id,
            request_hash=request_hash,
            broker_seq=broker_seq,
            response=response,
            occurred_at=occurred_at,
            outcome="ORDER_CREATED",
            attempt_outcome="CONSUMED_ORDER_CREATED",
            reason_code=None,
            order_id=order_id,
        )
        connection.execute(
            "INSERT INTO paper_orders"
            "(order_id,account_id,command_scope,idempotency_key,client_order_id,"
            "authorization_id,symbol,side,order_type,time_in_force,quantity,limit_price,"
            "filled_quantity,held_asset,held_amount,status,accepted_broker_seq,version) "
            "VALUES (%s,%s,'CREATE_ORDER',%s,%s,%s,%s,%s,'LIMIT','GTC',%s,%s,0,%s,%s,"
            "'OPEN',%s,1)",
            (
                order_id,
                account_id,
                authorization_id,
                client_order_id,
                authorization_id,
                symbol,
                side_text,
                quantity,
                limit_price,
                held_asset,
                held_amount,
                broker_seq,
            ),
        )
        updated = connection.execute(
            "UPDATE paper_asset_balances SET available=%s,held=%s,version=%s "
            "WHERE account_id=%s AND asset=%s AND version=%s",
            (
                subtract(available, held_amount),
                add(held, held_amount),
                balance_version + 1,
                account_id,
                held_asset,
                balance_version,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("PAPER_BALANCE_VERSION_CONFLICT")
        accepted = _phase7_paper_outbox(
            "paper.order.accepted.v2",
            "paper_order",
            order_id,
            1,
            order_data,
            occurred_at,
        )
        connection.execute(
            "INSERT INTO paper_order_events"
            "(event_id,order_id,order_version,event_type,source_key,payload_hash,occurred_at) "
            "VALUES (%s,%s,1,'paper.order.accepted.v2',%s,%s,%s)",
            (
                accepted.event_id,
                order_id,
                authorization_id,
                accepted.payload_hash,
                occurred_at,
            ),
        )
        connection.execute(
            "INSERT INTO paper_ledger_transactions"
            "(transaction_id,account_id,business_event_type,business_event_id,journal_kind,"
            "posted_at) VALUES (%s,%s,'paper.hold',%s,'PHYSICAL',%s)",
            (journal_id, account_id, order_id, occurred_at),
        )
        connection.execute(
            "INSERT INTO paper_ledger_entries"
            "(transaction_id,line_no,account_code,commodity,debit,credit) VALUES "
            "(%s,0,'paper.held',%s,%s,0),(%s,1,'paper.available',%s,0,%s)",
            (journal_id, held_asset, held_amount, journal_id, held_asset, held_amount),
        )
        for event in (
            _phase7_paper_outbox(
                "paper.authorization.consumed.v2",
                "paper_authorization",
                authorization_id,
                1,
                {
                    "authorization_id": authorization_id,
                    "authorization_nonce": authorization_nonce,
                    "approval_id": approval_id,
                    "request_hash": request_hash,
                    "outcome": "CONSUMED_ORDER_CREATED",
                    "order_id": order_id,
                },
                occurred_at,
            ),
            accepted,
            _phase7_paper_outbox(
                "ledger.transaction.posted.v2",
                "paper_ledger",
                journal_id,
                1,
                {"transaction_id": journal_id, "authorization_id": authorization_id},
                occurred_at,
            ),
        ):
            self._append_phase7_outbox(connection, account_id, event)
        return CommitResult(
            True,
            response,
            self.semantic_digest(account_id, connection=connection),
        )

    def cancel_phase7_order(
        self,
        order_id: str,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
        reason: str,
    ) -> CommitResult:
        """Cancel one production Paper order without accepting financial inputs."""
        if expected_version < 1:
            raise ValueError("INVALID_EXPECTED_VERSION")
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        if len(request_hash) != 64 or any(
            character not in "0123456789abcdef" for character in request_hash
        ):
            raise ValueError("INVALID_REQUEST_HASH")
        if not 1 <= len(reason) <= 512:
            raise ValueError("INVALID_CANCELLATION_REASON")

        with psycopg.connect(self.database_url) as connection:
            identity = connection.execute(
                "SELECT account_id FROM paper_orders WHERE order_id=%s", (order_id,)
            ).fetchone()
            if identity is None:
                raise KeyError("PAPER_ORDER_NOT_FOUND")
            account_id = identity[0]
            for lock_key in (
                f"paper-account:{account_id}",
                f"command:CANCEL_ORDER:{idempotency_key}",
            ):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,)
                )
            prior = connection.execute(
                "SELECT request_hash,order_id,response FROM paper_cancel_command_receipts "
                "WHERE idempotency_key=%s",
                (idempotency_key,),
            ).fetchone()
            if prior is not None:
                if prior[0] != request_hash or prior[1] != order_id:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return CommitResult(
                    False,
                    prior[2],
                    self.semantic_digest(account_id, connection=connection),
                )

            # Participate in the same global Kill serialization boundary. An active Kill
            # never blocks a risk-reducing cancellation.
            connection.execute("SELECT active,version FROM paper_lock_kill_barrier()").fetchone()
            order = connection.execute(
                "SELECT orders.client_order_id,orders.authorization_id,orders.symbol,"
                "orders.side,orders.order_type,orders.time_in_force,orders.quantity,"
                "orders.limit_price,orders.filled_quantity,orders.held_asset,"
                "orders.held_amount,orders.status,orders.version,authz.authorization_nonce,"
                "authz.approval_id,authz.proposal_hash,authz.risk_decision_hash,"
                "authz.paper_order_preview_hash FROM paper_orders orders "
                "JOIN paper_execution_authorizations authz "
                "ON authz.authorization_id=orders.authorization_id "
                "WHERE orders.order_id=%s AND orders.account_id=%s FOR UPDATE OF orders",
                (order_id, account_id),
            ).fetchone()
            if order is None:
                raise KeyError("PAPER_ORDER_NOT_FOUND")
            (
                client_order_id,
                authorization_id,
                symbol,
                side,
                order_type,
                time_in_force,
                quantity,
                limit_price,
                filled_quantity,
                held_asset,
                held_amount,
                status,
                version,
                authorization_nonce,
                approval_id,
                proposal_hash,
                risk_decision_hash,
                preview_hash,
            ) = order
            if version != expected_version:
                raise ValueError("ORDER_VERSION_MISMATCH")
            if status not in {"OPEN", "PARTIALLY_FILLED"}:
                raise ValueError("ORDER_NOT_CANCELLABLE")
            balance = cast(
                tuple[Decimal, Decimal, int] | None,
                connection.execute(
                    "SELECT available,held,version FROM paper_asset_balances "
                    "WHERE account_id=%s AND asset=%s FOR UPDATE",
                    (account_id, held_asset),
                ).fetchone(),
            )
            if balance is None or balance[1] < held_amount:
                raise RuntimeError("PAPER_CANCEL_HELD_BALANCE_UNDERFLOW")
            available, held, balance_version = balance
            now_row = connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()
            assert now_row is not None
            occurred_at = now_row[0].astimezone(UTC)
            new_version = version + 1
            response: dict[str, object] = {
                "result": "ORDER_CANCELLED",
                "order_id": order_id,
                "status": "CANCELLED",
                "version": new_version,
            }
            order_data: dict[str, object] = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "authorization_id": authorization_id,
                "authorization_namespace": "paper",
                "authorization_nonce": authorization_nonce,
                "approval_id": approval_id,
                "proposal_hash": proposal_hash,
                "risk_decision_hash": risk_decision_hash,
                "paper_order_preview_hash": preview_hash,
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "time_in_force": time_in_force,
                "quantity": canonical(quantity),
                "limit_price": canonical(limit_price),
                "filled_quantity": canonical(filled_quantity),
                "status": "CANCELLED",
                "version": new_version,
            }
            cancelled = _phase7_paper_outbox(
                "paper.order.cancelled.v2",
                "paper_order",
                order_id,
                new_version,
                {"order": order_data, "request_hash": request_hash, "reason": reason},
                occurred_at,
            )
            release_id = _engine_id("hold-release-journal", order_id)
            ledger_event = _phase7_paper_outbox(
                "ledger.transaction.posted.v2",
                "paper_ledger",
                release_id,
                1,
                {"transaction_id": release_id, "authorization_id": authorization_id},
                occurred_at,
            )

            connection.execute("SET CONSTRAINTS ALL DEFERRED")
            connection.execute(
                "INSERT INTO paper_cancel_command_receipts"
                "(idempotency_key,request_hash,order_id,expected_version,response,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    idempotency_key,
                    request_hash,
                    order_id,
                    expected_version,
                    Jsonb(response),
                    occurred_at,
                ),
            )
            balance_update = connection.execute(
                "UPDATE paper_asset_balances SET available=%s,held=%s,version=%s "
                "WHERE account_id=%s AND asset=%s AND version=%s",
                (
                    add(available, held_amount),
                    subtract(held, held_amount),
                    balance_version + 1,
                    account_id,
                    held_asset,
                    balance_version,
                ),
            )
            if balance_update.rowcount != 1:
                raise RuntimeError("PAPER_CANCEL_BALANCE_VERSION_CONFLICT")
            order_update = connection.execute(
                "UPDATE paper_orders SET held_amount=0,status='CANCELLED',version=%s "
                "WHERE order_id=%s AND version=%s",
                (new_version, order_id, expected_version),
            )
            if order_update.rowcount != 1:
                raise RuntimeError("PAPER_CANCEL_ORDER_VERSION_CONFLICT")
            connection.execute(
                "INSERT INTO paper_order_events"
                "(event_id,order_id,order_version,event_type,source_key,payload_hash,occurred_at) "
                "VALUES (%s,%s,%s,'paper.order.cancelled.v2',%s,%s,%s)",
                (
                    cancelled.event_id,
                    order_id,
                    new_version,
                    idempotency_key,
                    cancelled.payload_hash,
                    occurred_at,
                ),
            )
            connection.execute(
                "INSERT INTO paper_ledger_transactions"
                "(transaction_id,account_id,business_event_type,business_event_id,"
                "journal_kind,posted_at) VALUES (%s,%s,'paper.hold-release',%s,'PHYSICAL',%s)",
                (release_id, account_id, order_id, occurred_at),
            )
            connection.execute(
                "INSERT INTO paper_ledger_entries"
                "(transaction_id,line_no,account_code,commodity,debit,credit) VALUES "
                "(%s,0,'paper.available',%s,%s,0),(%s,1,'paper.held',%s,0,%s)",
                (release_id, held_asset, held_amount, release_id, held_asset, held_amount),
            )
            self._append_phase7_outbox(connection, account_id, cancelled)
            self._append_phase7_outbox(connection, account_id, ledger_event)
            return CommitResult(
                True,
                response,
                self.semantic_digest(account_id, connection=connection),
            )

    @staticmethod
    def _fail(stage: PersistenceStage, requested: PersistenceStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_PAPER_FAILURE:{stage.value}")

    def commit(
        self,
        write: AtomicPaperWrite,
        *,
        _fail_after: PersistenceStage | None = None,
        _after_barrier_acquired: Callable[[], None] | None = None,
    ) -> CommitResult:
        if write.namespace == "paper":
            if write.receipt is None or write.authorization_attempt is None:
                raise ValueError("PRODUCTION_AUTHORIZATION_ATTEMPT_REQUIRED")
            if (
                write.receipt.authorization_id != write.authorization_attempt.authorization_id
                or write.receipt.request_hash != write.authorization_attempt.request_hash
            ):
                raise ValueError("PRODUCTION_AUTHORIZATION_BINDING_CONFLICT")
            return self.attempt_phase7_authorization(write.authorization_attempt.authorization_id)
        with psycopg.connect(self.database_url) as connection:
            for item in write.broker_inputs:
                _validate_broker_input(item)
            receipt = write.receipt
            lock_keys = [
                f"observation:{item.source_key}"
                for item in write.broker_inputs
                if item.source_kind == "RECORDED_BOOK"
            ]
            if receipt is not None:
                lock_keys.append(f"command:{receipt.scope}:{receipt.idempotency_key}")
            for lock_key in sorted(lock_keys):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (lock_key,),
                )
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
                    "available_quantity,symbol,best_bid,best_ask FROM paper_broker_inputs "
                    "WHERE source_key=ANY(%s)",
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
                            item.symbol,
                            item.best_bid,
                            item.best_ask,
                        )
                        for item in write.broker_inputs
                    }
                    if not set(prior_inputs).issubset(expected_inputs):
                        raise ValueError("BROKER_INPUT_CONFLICT")
                recorded_inputs = tuple(
                    item for item in write.broker_inputs if item.source_kind == "RECORDED_BOOK"
                )
                if not recorded_inputs or write.order is None:
                    raise ValueError("OBSERVATION_EFFECT_ORDER_REQUIRED")
                prior_effects = connection.execute(
                    "SELECT source_key,effect_kind,fill_id FROM paper_observation_effects "
                    "WHERE order_id=%s AND source_key=ANY(%s)",
                    (write.order.order_id, [item.source_key for item in recorded_inputs]),
                ).fetchall()
                expected_effects = {
                    (
                        item.source_key,
                        "FILL" if matching_fill is not None else "NO_FILL",
                        matching_fill.fill_id if matching_fill is not None else None,
                    )
                    for item in recorded_inputs
                    for matching_fill in (
                        next(
                            (
                                candidate
                                for candidate in write.fills
                                if candidate.order_id == write.order.order_id
                                and candidate.observation_id == item.source_key
                            ),
                            None,
                        ),
                    )
                }
                if prior_effects:
                    if set(prior_effects) != expected_effects:
                        raise ValueError("OBSERVATION_EFFECT_CONFLICT")
                    return CommitResult(
                        False,
                        {"status": "OBSERVATION_APPLIED"},
                        self.semantic_digest(write.account_id, connection=connection),
                    )
            else:
                raise ValueError("PAPER_WRITE_IDEMPOTENCY_SOURCE_REQUIRED")

            requires_open_barrier = (
                (receipt is not None and receipt.outcome == "ORDER_CREATED")
                or bool(write.fills)
                or any(item.source_kind == "RECORDED_BOOK" for item in write.broker_inputs)
            )
            if requires_open_barrier:
                barrier = connection.execute(
                    "SELECT active,version FROM paper_lock_kill_barrier()"
                ).fetchone()
                if barrier is None:
                    raise RuntimeError("PAPER_KILL_BARRIER_MISSING")
                if barrier[0]:
                    raise RuntimeError("PAPER_KILL_SWITCH_ACTIVE")
                if barrier[1] != write.kill_switch_version:
                    raise RuntimeError("PAPER_KILL_VERSION_MISMATCH")
                if _after_barrier_acquired is not None:
                    _after_barrier_acquired()
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
                    "available_quantity,symbol,best_bid,best_ask) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (source_key) DO NOTHING",
                    (
                        item.broker_seq,
                        item.account_id,
                        item.source_kind,
                        item.source_key,
                        item.payload_hash,
                        item.observed_at,
                        item.available_quantity,
                        item.symbol,
                        item.best_bid,
                        item.best_ask,
                    ),
                )
                persisted_input = connection.execute(
                    "SELECT broker_seq,account_id,source_kind,payload_hash,available_quantity,"
                    "symbol,best_bid,best_ask "
                    "FROM paper_broker_inputs WHERE source_key=%s",
                    (item.source_key,),
                ).fetchone()
                if persisted_input != (
                    item.broker_seq,
                    item.account_id,
                    item.source_kind,
                    item.payload_hash,
                    item.available_quantity,
                    item.symbol,
                    item.best_bid,
                    item.best_ask,
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
            if write.order is not None:
                for item in write.broker_inputs:
                    if item.source_kind != "RECORDED_BOOK":
                        continue
                    matching_fill = next(
                        (
                            fill
                            for fill in write.fills
                            if fill.order_id == write.order.order_id
                            and fill.observation_id == item.source_key
                        ),
                        None,
                    )
                    connection.execute(
                        "INSERT INTO paper_observation_effects"
                        "(account_id,source_key,order_id,effect_kind,fill_id,applied_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (
                            write.account_id,
                            item.source_key,
                            write.order.order_id,
                            "FILL" if matching_fill is not None else "NO_FILL",
                            matching_fill.fill_id if matching_fill is not None else None,
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

    @staticmethod
    def _kill_fail(stage: KillCancelStage, requested: KillCancelStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_KILL_CANCEL_FAILURE:{stage.value}")

    def consume_kill_activation(
        self,
        activation_event_id: str,
        payload_hash: str,
        *,
        received_at: datetime,
        _fail_after: KillCancelStage | None = None,
    ) -> KillCancelResult:
        """Consume one activation and atomically cancel at most 100 Paper orders."""
        if received_at.tzinfo is None:
            raise ValueError("RECEIVED_AT_MUST_BE_AWARE")
        if (
            len(activation_event_id) != 64
            or any(character not in "0123456789abcdef" for character in activation_event_id)
            or len(payload_hash) != 64
            or any(character not in "0123456789abcdef" for character in payload_hash)
        ):
            raise ValueError("INVALID_KILL_ACTIVATION_IDENTITY")

        with psycopg.connect(self.database_url) as connection:
            account = connection.execute(
                "SELECT account_id FROM paper_orders "
                "WHERE status IN ('OPEN','PARTIALLY_FILLED') "
                "ORDER BY account_id,accepted_broker_seq,client_order_id,order_id LIMIT 1"
            ).fetchone()
            if account is None:
                account = connection.execute(
                    "SELECT paper_account_id FROM paper_kill_cancel_batches "
                    "WHERE activation_event_id=%s ORDER BY completed_at DESC,batch_key DESC LIMIT 1",
                    (activation_event_id,),
                ).fetchone()
            account_id = account[0] if account is not None else PAPER_DEFAULT_ACCOUNT_ID
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"paper-account:{account_id}",),
            )
            barrier = connection.execute(
                "SELECT active,version,last_activation_event_id FROM paper_lock_kill_barrier()"
            ).fetchone()
            if barrier is None or not barrier[0] or barrier[2] != activation_event_id:
                raise RuntimeError("KILL_ACTIVATION_NOT_AUTHORITATIVE")
            durable_event = connection.execute(
                "SELECT paper_validate_kill_activation_v1(%s,%s)",
                (activation_event_id, payload_hash),
            ).fetchone()
            if durable_event != (True,):
                raise ValueError("KILL_ACTIVATION_PAYLOAD_CONFLICT")
            cursor = connection.execute(
                "INSERT INTO paper_kill_inbox(activation_event_id,payload_hash,received_at) "
                "VALUES (%s,%s,%s) ON CONFLICT (activation_event_id) DO NOTHING",
                (activation_event_id, payload_hash, received_at),
            )
            inbox_created = cursor.rowcount == 1
            if not inbox_created:
                prior_hash = connection.execute(
                    "SELECT payload_hash FROM paper_kill_inbox WHERE activation_event_id=%s",
                    (activation_event_id,),
                ).fetchone()
                if prior_hash != (payload_hash,):
                    raise ValueError("KILL_ACTIVATION_PAYLOAD_CONFLICT")
            self._kill_fail(KillCancelStage.INBOX, _fail_after)

            account = connection.execute(
                "SELECT account_id FROM paper_orders "
                "WHERE status IN ('OPEN','PARTIALLY_FILLED') "
                "ORDER BY account_id,accepted_broker_seq,client_order_id,order_id LIMIT 1"
            ).fetchone()
            if account is None:
                completion_created = self._complete_kill_activation(
                    connection,
                    activation_event_id=activation_event_id,
                    payload_hash=payload_hash,
                    account_id=account_id,
                    completed_at=received_at,
                )
                return KillCancelResult(
                    inbox_created,
                    False,
                    activation_event_id,
                    None,
                    account_id,
                    0,
                    False,
                    completion_created,
                )
            if account[0] != account_id:
                raise RuntimeError("KILL_CANCEL_ACCOUNT_LOCK_DRIFT")
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"kill-cancel:{activation_event_id}:{account_id}",),
            )
            orders = connection.execute(
                "SELECT order_id,accepted_broker_seq,client_order_id,held_asset,held_amount,version "
                "FROM paper_orders WHERE account_id=%s "
                "AND status IN ('OPEN','PARTIALLY_FILLED') "
                "ORDER BY account_id,accepted_broker_seq,client_order_id,order_id "
                "LIMIT 100 FOR UPDATE",
                (account_id,),
            ).fetchall()
            if not orders:
                has_more_row = connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM paper_orders "
                    "WHERE status IN ('OPEN','PARTIALLY_FILLED'))"
                ).fetchone()
                assert has_more_row is not None
                has_more = has_more_row[0]
                completion_created = False
                if not has_more:
                    completion_created = self._complete_kill_activation(
                        connection,
                        activation_event_id=activation_event_id,
                        payload_hash=payload_hash,
                        account_id=account_id,
                        completed_at=received_at,
                    )
                return KillCancelResult(
                    inbox_created,
                    False,
                    activation_event_id,
                    None,
                    account_id,
                    0,
                    has_more,
                    completion_created,
                )

            first_cursor = [account_id, orders[0][1], orders[0][2], orders[0][0]]
            last_cursor = [account_id, orders[-1][1], orders[-1][2], orders[-1][0]]
            batch_key = _digest([activation_event_id, account_id, first_cursor, last_cursor])
            connection.execute(
                "INSERT INTO paper_kill_cancel_batches"
                "(activation_event_id,batch_key,paper_account_id,first_cursor,last_cursor,"
                "cancelled_count,completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    activation_event_id,
                    batch_key,
                    account_id,
                    Jsonb(first_cursor),
                    Jsonb(last_cursor),
                    len(orders),
                    received_at,
                ),
            )
            self._kill_fail(KillCancelStage.BATCH, _fail_after)

            for order_id, _, _, held_asset, held_amount, order_version in orders:
                balance = connection.execute(
                    "SELECT available,held,version FROM paper_asset_balances "
                    "WHERE account_id=%s AND asset=%s FOR UPDATE",
                    (account_id, held_asset),
                ).fetchone()
                if balance is None or balance[1] < held_amount:
                    raise RuntimeError("KILL_CANCEL_HELD_BALANCE_UNDERFLOW")
                connection.execute(
                    "UPDATE paper_asset_balances SET available=available+%s,held=held-%s,"
                    "version=version+1 WHERE account_id=%s AND asset=%s",
                    (held_amount, held_amount, account_id, held_asset),
                )
                new_version = order_version + 1
                connection.execute(
                    "UPDATE paper_orders SET status='CANCELLED',held_amount=0,version=%s "
                    "WHERE order_id=%s",
                    (new_version, order_id),
                )
                cancel_id = kill_cancel_id(activation_event_id, order_id)
                connection.execute(
                    "INSERT INTO paper_kill_cancel_items"
                    "(activation_event_id,order_id,batch_key,paper_account_id,cancel_id,"
                    "released_asset,released_amount,cancelled_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        activation_event_id,
                        order_id,
                        batch_key,
                        account_id,
                        cancel_id,
                        held_asset,
                        held_amount,
                        received_at,
                    ),
                )
                data = {"order_id": order_id, "cancel_id": cancel_id}
                event_type = "paper.order.cancelled.v1"
                outbox_event_id = _engine_id("event", event_type, order_id, new_version)
                outbox_payload_hash = _engine_id(
                    "event-payload",
                    event_type,
                    order_id,
                    new_version,
                    json.dumps(data, sort_keys=True, separators=(",", ":")),
                )
                source_key = f"kill:{activation_event_id}"
                connection.execute(
                    "INSERT INTO paper_order_events"
                    "(event_id,order_id,order_version,event_type,source_key,payload_hash,occurred_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        outbox_event_id,
                        order_id,
                        new_version,
                        event_type,
                        source_key,
                        outbox_payload_hash,
                        received_at,
                    ),
                )
                journal_id = _engine_id("paper.hold-release", order_id, "PHYSICAL")
                connection.execute(
                    "INSERT INTO paper_ledger_transactions"
                    "(transaction_id,account_id,business_event_type,business_event_id,"
                    "journal_kind,reversal_of,replacement_for,posted_at) "
                    "VALUES (%s,%s,'paper.hold-release',%s,'PHYSICAL',NULL,NULL,%s)",
                    (journal_id, account_id, order_id, received_at),
                )
                connection.execute(
                    "INSERT INTO paper_ledger_entries"
                    "(transaction_id,line_no,account_code,commodity,debit,credit) VALUES "
                    "(%s,0,'paper.available',%s,%s,0),"
                    "(%s,1,'paper.held',%s,0,%s)",
                    (journal_id, held_asset, held_amount, journal_id, held_asset, held_amount),
                )
                envelope = {
                    "spec_version": "woozoo.event/v1",
                    "event_id": outbox_event_id,
                    "event_type": event_type,
                    "event_version": 1,
                    "occurred_at": received_at.isoformat(),
                    "producer": "paper-engine",
                    "activation_phase": 7,
                    "aggregate_id": order_id,
                    "aggregate_version": new_version,
                    "payload_hash": outbox_payload_hash,
                    "data": data,
                }
                connection.execute(
                    "SELECT append_paper_outbox(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        outbox_event_id,
                        event_type,
                        Jsonb(envelope),
                        outbox_payload_hash,
                        received_at,
                        "paper_order",
                        order_id,
                        new_version,
                    ),
                )
                connection.execute(
                    "INSERT INTO paper_outbox_links(event_id,account_id) VALUES (%s,%s)",
                    (outbox_event_id, account_id),
                )
            self._kill_fail(KillCancelStage.DOMAIN, _fail_after)
            self._kill_fail(KillCancelStage.LEDGER, _fail_after)
            self._kill_fail(KillCancelStage.OUTBOX, _fail_after)
            has_more_row = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM paper_orders "
                "WHERE status IN ('OPEN','PARTIALLY_FILLED'))"
            ).fetchone()
            assert has_more_row is not None
            has_more = has_more_row[0]
            completion_created = False
            if not has_more:
                completion_created = self._complete_kill_activation(
                    connection,
                    activation_event_id=activation_event_id,
                    payload_hash=payload_hash,
                    account_id=account_id,
                    completed_at=received_at,
                )
        return KillCancelResult(
            inbox_created,
            True,
            activation_event_id,
            batch_key,
            account_id,
            len(orders),
            has_more,
            completion_created,
        )

    def _complete_kill_activation(
        self,
        connection: psycopg.Connection[object],
        *,
        activation_event_id: str,
        payload_hash: str,
        account_id: str,
        completed_at: datetime,
    ) -> bool:
        if connection.execute(
            "SELECT EXISTS(SELECT 1 FROM paper_orders WHERE status IN ('OPEN','PARTIALLY_FILLED'))"
        ).fetchone() != (False,):
            raise RuntimeError("KILL_CANCELLATION_STILL_HAS_OPEN_ORDERS")
        counts = cast(
            tuple[int, int] | None,
            connection.execute(
                "SELECT count(*),COALESCE(sum(cancelled_count),0) "
                "FROM paper_kill_cancel_batches WHERE activation_event_id=%s",
                (activation_event_id,),
            ).fetchone(),
        )
        assert counts is not None
        state_digest = self.semantic_digest(account_id, connection=connection)
        inserted = connection.execute(
            "INSERT INTO paper_kill_cancel_completions"
            "(activation_event_id,payload_hash,paper_account_id,batch_count,cancelled_count,"
            "state_digest,completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (activation_event_id) DO NOTHING",
            (
                activation_event_id,
                payload_hash,
                account_id,
                counts[0],
                counts[1],
                state_digest,
                completed_at,
            ),
        )
        if inserted.rowcount == 1:
            return True
        prior = connection.execute(
            "SELECT payload_hash,paper_account_id,batch_count,cancelled_count,state_digest "
            "FROM paper_kill_cancel_completions WHERE activation_event_id=%s",
            (activation_event_id,),
        ).fetchone()
        if prior != (payload_hash, account_id, counts[0], counts[1], state_digest):
            raise RuntimeError("KILL_COMPLETION_REPLAY_CONFLICT")
        return False

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
                    "SELECT broker_seq,source_kind,source_key,payload_hash,symbol,"
                    "best_bid,best_ask,available_quantity FROM "
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
                "observation_effects": (
                    "SELECT source_key,order_id,effect_kind,fill_id FROM "
                    "paper_observation_effects WHERE account_id=%s ORDER BY source_key,order_id"
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
            engine.observation_effects = {
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT order_id,source_key FROM paper_observation_effects WHERE account_id=%s",
                    (account_id,),
                ).fetchall()
            }
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
            for (
                source_key,
                broker_seq,
                payload_hash,
                available_quantity,
                symbol,
                best_bid,
                best_ask,
                allocated,
            ) in connection.execute(
                "SELECT input.source_key,input.broker_seq,input.payload_hash,input.available_quantity,"
                "input.symbol,input.best_bid,input.best_ask,"
                "COALESCE(sum(fill.quantity),0) FROM paper_broker_inputs input LEFT JOIN "
                "paper_fills fill ON fill.source_key=input.source_key "
                "WHERE input.account_id=%s AND input.source_kind='RECORDED_BOOK' "
                "GROUP BY input.source_key,input.broker_seq,input.payload_hash,input.available_quantity,"
                "input.symbol,input.best_bid,input.best_ask",
                (account_id,),
            ).fetchall():
                if symbol not in SYMBOL_RULES or payload_hash != _engine_id(
                    "observation",
                    symbol,
                    canonical(best_bid),
                    canonical(best_ask),
                    canonical(available_quantity),
                ):
                    raise ValueError("CORRUPT_RECORDED_BOOK_INPUT")
                engine.observation_budgets[source_key] = (
                    payload_hash,
                    subtract(
                        floor_product_to_step(
                            available_quantity,
                            PARTICIPATION_RATE,
                            step=SYMBOL_RULES[symbol]["step"],
                        ),
                        allocated,
                    ),
                )
                engine.observation_sequences[source_key] = broker_seq
            engine.outbox = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT payload FROM paper_outbox_events_v1 WHERE account_id=%s "
                    "ORDER BY occurred_at,event_id",
                    (account_id,),
                ).fetchall()
            )
            max_sequence = connection.execute(
                "SELECT greatest("
                "COALESCE((SELECT max(broker_seq) FROM paper_broker_inputs WHERE account_id=%s),0),"
                "COALESCE((SELECT max(fill.broker_seq) FROM paper_fills fill JOIN paper_orders "
                "paper_order USING(order_id) WHERE paper_order.account_id=%s),0))",
                (account_id, account_id),
            ).fetchone()
            assert max_sequence is not None
            engine.broker_seq = max_sequence[0]
        return engine

    def reconcile(
        self, account_id: str, *, checkpoint_id: str, created_at: datetime
    ) -> ReconciliationResult:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"paper-account:{account_id}",),
            )
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
            for payload_hash, symbol, best_bid, best_ask, available_quantity in connection.execute(
                "SELECT payload_hash,symbol,best_bid,best_ask,available_quantity "
                "FROM paper_broker_inputs WHERE account_id=%s "
                "AND source_kind='RECORDED_BOOK'",
                (account_id,),
            ).fetchall():
                if symbol not in SYMBOL_RULES or payload_hash != _engine_id(
                    "observation",
                    symbol,
                    canonical(best_bid),
                    canonical(best_ask),
                    canonical(available_quantity),
                ):
                    mismatches.append("BROKER_INPUT_HASH_MISMATCH")
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
