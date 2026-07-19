from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Barrier
from typing import Iterator

import psycopg
import pytest

from paper_engine.models import (
    FifoLot,
    Journal,
    LedgerEntry,
    LotConsumption,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
)
from paper_engine.persistence import (
    AtomicPaperWrite,
    AuthorizationAttemptWrite,
    BalanceWrite,
    BrokerInputWrite,
    CommitResult,
    CommandReceiptWrite,
    OrderEventWrite,
    OutboxWrite,
    PostgresPaperStore,
)


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
PAPER_WRITER_URL = "postgresql://woozoo_paper_engine@127.0.0.1:5433/woozoo"
EVIDENCE_WRITER_URL = "postgresql://woozoo_evidence_writer@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


@contextmanager
def infrastructure_lock() -> Iterator[None]:
    path = ROOT / ".p1-integration.lock"
    with path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("could not acquire the infrastructure lock")
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@pytest.fixture(scope="module", autouse=True)
def postgres() -> Iterator[None]:
    with infrastructure_lock():
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "downgrade", "20260719_0003")
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            yield
        finally:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            run("docker", "compose", "stop", "postgres")


def digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def stable_id(value: str) -> str:
    return digest(value)


def engine_id(kind: str, *parts: object) -> str:
    return digest(json.dumps([kind, *[str(part) for part in parts]], separators=(",", ":")))


def outbox(event_type: str, aggregate_id: str, version: int, data: dict[str, str]) -> OutboxWrite:
    event_id = engine_id("event", event_type, aggregate_id, version)
    payload_hash = stable_id(
        json.dumps(
            [
                "event-payload",
                event_type,
                aggregate_id,
                str(version),
                json.dumps(data, sort_keys=True, separators=(",", ":")),
            ],
            separators=(",", ":"),
        )
    )
    payload = {
        "spec_version": "woozoo.event/v1",
        "event_id": event_id,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": NOW.isoformat(),
        "producer": "paper-engine",
        "activation_phase": 7,
        "aggregate_id": aggregate_id,
        "aggregate_version": version,
        "payload_hash": payload_hash,
        "data": data,
    }
    aggregate_type = (
        "paper_authorization"
        if event_type == "paper.authorization.attempted.v1"
        else "paper_request"
        if event_type == "paper.order.rejected.v1"
        else "paper_ledger"
        if event_type == "ledger.transaction.posted.v1"
        else "paper_order"
    )
    return OutboxWrite(
        event_id=event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=version,
        payload=payload,
        payload_hash=payload_hash,
        occurred_at=NOW,
    )


def complete_write(*, suffix: str = "base") -> AtomicPaperWrite:
    seq_base = int(digest(suffix)[:8], 16)
    account_id = f"account-{suffix}"
    order_id = stable_id(f"order-{suffix}")
    fill_id = stable_id(f"fill-{suffix}")
    request_hash = digest(f"request-{suffix}")
    command_key = f"create-{suffix}"
    command_source = f"command:{suffix}"
    book_source = f"book:{suffix}"
    book_payload_hash = engine_id(
        "observation",
        "BTCUSDT",
        "99.000000000000000000",
        "100.000000000000000000",
        "0.100000000000000000",
    )
    receipt = CommandReceiptWrite(
        scope="paper.create.v1",
        idempotency_key=command_key,
        account_id=account_id,
        request_hash=request_hash,
        outcome="ORDER_CREATED",
        paper_order_id=order_id,
        authorization_id=f"auth-{suffix}",
        broker_seq=seq_base,
        response={"order_id": order_id, "status": "PARTIALLY_FILLED"},
        created_at=NOW,
    )

    attempt = AuthorizationAttemptWrite(
        authorization_id=f"auth-{suffix}",
        authorization_nonce=f"nonce-{suffix}",
        account_id=account_id,
        command_scope=receipt.scope,
        idempotency_key=command_key,
        namespace="test",
        request_hash=request_hash,
        outcome="CONSUMED_ORDER_CREATED",
        reason_code=None,
        created_at=NOW,
    )
    order = PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{suffix}",
        authorization_id=attempt.authorization_id,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.010000000000000000"),
        limit_price=Decimal("10000.000000000000000000"),
        accepted_broker_seq=seq_base,
        status=OrderStatus.PARTIALLY_FILLED,
        filled_quantity=Decimal("0.005000000000000000"),
        held_asset="USDT",
        held_amount=Decimal("50.050000000000000000"),
        version=2,
    )
    fill = PaperFill(
        fill_id=fill_id,
        order_id=order_id,
        observation_id=book_source,
        quantity=Decimal("0.005000000000000000"),
        price=Decimal("10000.000000000000000000"),
        fee_asset="USDT",
        fee_rate=Decimal("0.001000000000000000"),
        fee_amount=Decimal("0.050000000000000000"),
        broker_seq=seq_base + 1,
    )
    lot = FifoLot(
        lot_id=f"lot-{suffix}",
        asset="BTC",
        acquired_quantity=fill.quantity,
        quote_cost=Decimal("50.050000000000000000"),
        source_fill_id=fill_id,
        acquired_at=NOW,
    )
    journals = (
        Journal(
            journal_id=f"seed-journal-{suffix}",
            business_event_type="paper.seed",
            business_event_id=f"seed-{suffix}",
            journal_kind="PHYSICAL",
            entries=(
                LedgerEntry("paper.available", "USDT", Decimal("200"), Decimal(0)),
                LedgerEntry("paper.opening-equity", "USDT", Decimal(0), Decimal("200")),
            ),
        ),
        Journal(
            journal_id=f"hold-journal-{suffix}",
            business_event_type="paper.hold",
            business_event_id=order_id,
            journal_kind="PHYSICAL",
            entries=(
                LedgerEntry("paper.held", "USDT", Decimal("100.1"), Decimal(0)),
                LedgerEntry("paper.available", "USDT", Decimal(0), Decimal("100.1")),
            ),
        ),
        Journal(
            journal_id=f"fill-journal-{suffix}",
            business_event_type="paper.fill",
            business_event_id=fill_id,
            journal_kind="PHYSICAL",
            entries=(
                LedgerEntry("paper.asset", "BTC", fill.quantity, Decimal(0)),
                LedgerEntry("exchange.clearing", "BTC", Decimal(0), fill.quantity),
                LedgerEntry("exchange.clearing", "USDT", Decimal("50"), Decimal(0)),
                LedgerEntry("paper.fee", "USDT", fill.fee_amount, Decimal(0)),
                LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("50.05")),
            ),
        ),
        Journal(
            journal_id=f"valuation-journal-{suffix}",
            business_event_type="paper.fill",
            business_event_id=fill_id,
            journal_kind="VALUATION",
            entries=(
                LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal("50.05"), Decimal(0)),
                LedgerEntry("paper.acquisition-value", "USDT_VAL", Decimal(0), Decimal("50.05")),
            ),
        ),
    )
    return AtomicPaperWrite(
        account_id=account_id,
        namespace="test",
        receipt=receipt,
        authorization_attempt=attempt,
        broker_inputs=(
            BrokerInputWrite(
                seq_base, account_id, "TEST_COMMAND", command_source, request_hash, NOW
            ),
            BrokerInputWrite(
                seq_base + 1,
                account_id,
                "RECORDED_BOOK",
                book_source,
                book_payload_hash,
                NOW,
                Decimal("0.100000000000000000"),
                "BTCUSDT",
                Decimal("99.000000000000000000"),
                Decimal("100.000000000000000000"),
            ),
        ),
        balances=(
            BalanceWrite("USDT", Decimal("99.9"), Decimal("50.05"), 1),
            BalanceWrite("BTC", Decimal("0.005"), Decimal(0), 1),
        ),
        order=order,
        order_events=(
            OrderEventWrite(
                stable_id(f"accepted-{suffix}"),
                order_id,
                1,
                "paper.order.accepted.v1",
                command_source,
                request_hash,
                NOW,
            ),
            OrderEventWrite(
                stable_id(f"partial-{suffix}"),
                order_id,
                2,
                "paper.order.partially-filled.v1",
                book_source,
                digest(fill_id),
                NOW,
            ),
        ),
        fills=(fill,),
        lots=(lot,),
        consumptions=(),
        journals=journals,
        outbox=(
            outbox("paper.order.accepted.v1", order_id, 1, {"order_id": order_id}),
            outbox(
                "paper.order.partially-filled.v1",
                order_id,
                2,
                {"order_id": order_id, "fill_id": fill_id},
            ),
        ),
        created_at=NOW,
    )


def cancel_write(initial: AtomicPaperWrite) -> AtomicPaperWrite:
    assert initial.order is not None and initial.receipt is not None
    seq = initial.order.accepted_broker_seq + 2
    cancel_id = f"cancel-{initial.receipt.idempotency_key}"
    request_hash = digest(cancel_id)
    source_key = f"command:{cancel_id}"
    receipt = CommandReceiptWrite(
        "paper.cancel.v1",
        cancel_id,
        initial.account_id,
        request_hash,
        "ORDER_CANCELLED",
        initial.order.order_id,
        f"auth-{cancel_id}",
        seq,
        {"order_id": initial.order.order_id, "status": "CANCELLED"},
        NOW,
    )
    attempt = AuthorizationAttemptWrite(
        receipt.authorization_id,
        f"nonce-{cancel_id}",
        initial.account_id,
        receipt.scope,
        receipt.idempotency_key,
        "test",
        request_hash,
        "CONSUMED_ORDER_CANCELLED",
        None,
        NOW,
    )
    order = replace(initial.order, status=OrderStatus.CANCELLED, held_amount=Decimal(0), version=3)
    release = Journal(
        stable_id(f"release-{order.order_id}"),
        "paper.hold-release",
        order.order_id,
        "PHYSICAL",
        (
            LedgerEntry("paper.available", "USDT", Decimal("50.05"), Decimal(0)),
            LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("50.05")),
        ),
    )
    return AtomicPaperWrite(
        initial.account_id,
        "test",
        receipt,
        attempt,
        (BrokerInputWrite(seq, initial.account_id, "TEST_COMMAND", source_key, request_hash, NOW),),
        (
            BalanceWrite("USDT", Decimal("149.95"), Decimal(0), 2),
            BalanceWrite("BTC", Decimal("0.005"), Decimal(0), 2),
        ),
        order,
        (
            OrderEventWrite(
                stable_id(f"cancelled-{order.order_id}"),
                order.order_id,
                3,
                "paper.order.cancelled.v1",
                source_key,
                request_hash,
                NOW,
            ),
        ),
        (),
        (),
        (),
        (release,),
        (
            outbox(
                "paper.order.cancelled.v1",
                order.order_id,
                3,
                {"order_id": order.order_id, "cancel_id": cancel_id},
            ),
        ),
        NOW,
    )


def rejected_write(*, suffix: str) -> AtomicPaperWrite:
    account_id = f"account-rejected-{suffix}"
    request_hash = digest(f"rejected-{suffix}")
    authorization_id = f"auth-rejected-{suffix}"
    seq = int(digest(suffix)[:8], 16)
    receipt = CommandReceiptWrite(
        "paper.create.v1",
        f"reject-{suffix}",
        account_id,
        request_hash,
        "REJECTED",
        None,
        authorization_id,
        seq,
        {"error_code": "INSUFFICIENT_FUNDS"},
        NOW,
    )
    attempt = AuthorizationAttemptWrite(
        authorization_id,
        f"nonce-rejected-{suffix}",
        account_id,
        receipt.scope,
        receipt.idempotency_key,
        "test",
        request_hash,
        "BLOCKED",
        "INSUFFICIENT_FUNDS",
        NOW,
    )
    auth_aggregate = engine_id("authorization", authorization_id)
    return AtomicPaperWrite(
        account_id,
        "test",
        receipt,
        attempt,
        (
            BrokerInputWrite(
                seq, account_id, "TEST_COMMAND", f"command:reject:{suffix}", request_hash, NOW
            ),
        ),
        (),
        None,
        (),
        (),
        (),
        (),
        (),
        (
            outbox(
                "paper.authorization.attempted.v1",
                auth_aggregate,
                1,
                {"authorization_id": authorization_id, "outcome": "BLOCKED"},
            ),
            outbox(
                "paper.order.rejected.v1",
                request_hash,
                1,
                {"request_hash": request_hash, "reason": "INSUFFICIENT_FUNDS"},
            ),
        ),
        NOW,
    )


def split_open_and_fill(*, suffix: str) -> tuple[AtomicPaperWrite, AtomicPaperWrite]:
    complete = complete_write(suffix=suffix)
    assert complete.order is not None
    opened_order = replace(
        complete.order,
        status=OrderStatus.OPEN,
        filled_quantity=Decimal(0),
        held_amount=Decimal("100.1"),
        version=1,
    )
    opened = replace(
        complete,
        broker_inputs=(complete.broker_inputs[0],),
        balances=(BalanceWrite("USDT", Decimal("99.9"), Decimal("100.1"), 1),),
        order=opened_order,
        order_events=(complete.order_events[0],),
        fills=(),
        lots=(),
        journals=complete.journals[:2],
        outbox=(complete.outbox[0],),
    )
    fill = replace(
        complete,
        receipt=None,
        authorization_attempt=None,
        broker_inputs=(complete.broker_inputs[1],),
        balances=(
            BalanceWrite("USDT", Decimal("99.9"), Decimal("50.05"), 2),
            BalanceWrite("BTC", Decimal("0.005"), Decimal(0), 1),
        ),
        order_events=(complete.order_events[1],),
        journals=complete.journals[2:],
        outbox=(complete.outbox[1],),
    )
    return opened, fill


def second_order_on_shared_observation(
    initial: AtomicPaperWrite,
) -> tuple[AtomicPaperWrite, AtomicPaperWrite]:
    assert initial.order is not None
    source = initial.broker_inputs[1]
    command_seq = initial.order.accepted_broker_seq + 1
    order_id = stable_id(f"second-order:{initial.account_id}")
    authorization_id = f"auth-second-{initial.account_id}"
    command_key = f"create-second-{initial.account_id}"
    request_hash = digest(command_key)
    command_source = f"command:{command_key}"
    receipt = CommandReceiptWrite(
        "paper.create.v1",
        command_key,
        initial.account_id,
        request_hash,
        "ORDER_CREATED",
        order_id,
        authorization_id,
        command_seq,
        {"order_id": order_id, "status": "OPEN"},
        NOW,
    )
    attempt = AuthorizationAttemptWrite(
        authorization_id,
        f"nonce-{command_key}",
        initial.account_id,
        receipt.scope,
        command_key,
        "test",
        request_hash,
        "CONSUMED_ORDER_CREATED",
        None,
        NOW,
    )
    order = PaperOrder(
        order_id,
        f"client-{command_key}",
        authorization_id,
        "BTCUSDT",
        OrderSide.BUY,
        Decimal("0.005"),
        Decimal("10000"),
        command_seq,
        OrderStatus.OPEN,
        Decimal(0),
        "USDT",
        Decimal("50.05"),
        1,
    )
    hold = Journal(
        stable_id(f"hold-{order_id}"),
        "paper.hold",
        order_id,
        "PHYSICAL",
        (
            LedgerEntry("paper.held", "USDT", Decimal("50.05"), Decimal(0)),
            LedgerEntry("paper.available", "USDT", Decimal(0), Decimal("50.05")),
        ),
    )
    opened = AtomicPaperWrite(
        initial.account_id,
        "test",
        receipt,
        attempt,
        (
            BrokerInputWrite(
                command_seq,
                initial.account_id,
                "TEST_COMMAND",
                command_source,
                request_hash,
                NOW,
            ),
        ),
        (BalanceWrite("USDT", Decimal("49.85"), Decimal("100.1"), 2),),
        order,
        (
            OrderEventWrite(
                stable_id(f"accepted-{order_id}"),
                order_id,
                1,
                "paper.order.accepted.v1",
                command_source,
                request_hash,
                NOW,
            ),
        ),
        (),
        (),
        (),
        (hold,),
        (outbox("paper.order.accepted.v1", order_id, 1, {"order_id": order_id}),),
        NOW,
    )
    fill_id = stable_id(f"shared-fill-{order_id}")
    filled_order = replace(
        order,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.005"),
        held_amount=Decimal(0),
        version=2,
    )
    fill = PaperFill(
        fill_id,
        order_id,
        source.source_key,
        Decimal("0.005"),
        Decimal("10000"),
        "USDT",
        Decimal("0.001"),
        Decimal("0.05"),
        source.broker_seq + 1,
    )
    lot = FifoLot(fill_id, "BTC", Decimal("0.005"), Decimal("50.05"), fill_id, NOW)
    physical = Journal(
        stable_id(f"physical-{fill_id}"),
        "paper.fill",
        fill_id,
        "PHYSICAL",
        (
            LedgerEntry("paper.asset", "BTC", Decimal("0.005"), Decimal(0)),
            LedgerEntry("exchange.clearing", "BTC", Decimal(0), Decimal("0.005")),
            LedgerEntry("exchange.clearing", "USDT", Decimal("50"), Decimal(0)),
            LedgerEntry("paper.fee", "USDT", Decimal("0.05"), Decimal(0)),
            LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("50.05")),
        ),
    )
    valuation = Journal(
        stable_id(f"valuation-{fill_id}"),
        "paper.fill",
        fill_id,
        "VALUATION",
        (
            LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal("50.05"), Decimal(0)),
            LedgerEntry("paper.acquisition-value", "USDT_VAL", Decimal(0), Decimal("50.05")),
        ),
    )
    filled = AtomicPaperWrite(
        initial.account_id,
        "test",
        None,
        None,
        (source,),
        (
            BalanceWrite("USDT", Decimal("49.85"), Decimal("50.05"), 3),
            BalanceWrite("BTC", Decimal("0.01"), Decimal(0), 2),
        ),
        filled_order,
        (
            OrderEventWrite(
                stable_id(f"filled-{order_id}"),
                order_id,
                2,
                "paper.order.filled.v1",
                source.source_key,
                digest(fill_id),
                NOW,
            ),
        ),
        (fill,),
        (lot,),
        (),
        (physical, valuation),
        (
            outbox(
                "paper.order.filled.v1",
                order_id,
                2,
                {"order_id": order_id, "fill_id": fill_id},
            ),
        ),
        NOW,
    )
    return opened, filled


def sell_order_after_cancel(
    initial: AtomicPaperWrite,
) -> tuple[AtomicPaperWrite, AtomicPaperWrite]:
    assert initial.order is not None and initial.receipt is not None
    command_seq = initial.order.accepted_broker_seq + 3
    order_id = stable_id(f"sell-order:{initial.account_id}")
    authorization_id = f"auth-sell-{initial.account_id}"
    command_key = f"create-sell-{initial.account_id}"
    request_hash = digest(command_key)
    command_source = f"command:{command_key}"
    receipt = CommandReceiptWrite(
        "paper.create.v1",
        command_key,
        initial.account_id,
        request_hash,
        "ORDER_CREATED",
        order_id,
        authorization_id,
        command_seq,
        {"order_id": order_id, "status": "OPEN"},
        NOW,
    )
    attempt = AuthorizationAttemptWrite(
        authorization_id,
        f"nonce-{command_key}",
        initial.account_id,
        receipt.scope,
        command_key,
        "test",
        request_hash,
        "CONSUMED_ORDER_CREATED",
        None,
        NOW,
    )
    order = PaperOrder(
        order_id,
        f"client-{command_key}",
        authorization_id,
        "BTCUSDT",
        OrderSide.SELL,
        Decimal("0.005"),
        Decimal("12000"),
        command_seq,
        OrderStatus.OPEN,
        Decimal(0),
        "BTC",
        Decimal("0.005"),
        1,
    )
    hold = Journal(
        stable_id(f"hold-{order_id}"),
        "paper.hold",
        order_id,
        "PHYSICAL",
        (
            LedgerEntry("paper.held", "BTC", Decimal("0.005"), Decimal(0)),
            LedgerEntry("paper.available", "BTC", Decimal(0), Decimal("0.005")),
        ),
    )
    opened = AtomicPaperWrite(
        initial.account_id,
        "test",
        receipt,
        attempt,
        (
            BrokerInputWrite(
                command_seq,
                initial.account_id,
                "TEST_COMMAND",
                command_source,
                request_hash,
                NOW,
            ),
        ),
        (BalanceWrite("BTC", Decimal(0), Decimal("0.005"), 3),),
        order,
        (
            OrderEventWrite(
                stable_id(f"accepted-{order_id}"),
                order_id,
                1,
                "paper.order.accepted.v1",
                command_source,
                request_hash,
                NOW,
            ),
        ),
        (),
        (),
        (),
        (hold,),
        (outbox("paper.order.accepted.v1", order_id, 1, {"order_id": order_id}),),
        NOW,
    )
    source_key = f"book:sell:{initial.account_id}"
    book_seq = command_seq + 1
    book = BrokerInputWrite(
        book_seq,
        initial.account_id,
        "RECORDED_BOOK",
        source_key,
        engine_id(
            "observation",
            "BTCUSDT",
            "12000.000000000000000000",
            "12001.000000000000000000",
            "0.050000000000000000",
        ),
        NOW,
        Decimal("0.05"),
        "BTCUSDT",
        Decimal("12000"),
        Decimal("12001"),
    )
    fill_id = stable_id(f"sell-fill:{order_id}")
    fill = PaperFill(
        fill_id,
        order_id,
        source_key,
        Decimal("0.005"),
        Decimal("12000"),
        "USDT",
        Decimal("0.001"),
        Decimal("0.06"),
        book_seq,
    )
    consumption = initial.lots[0]
    physical = Journal(
        stable_id(f"physical-{fill_id}"),
        "paper.fill",
        fill_id,
        "PHYSICAL",
        (
            LedgerEntry("exchange.clearing", "BTC", Decimal("0.005"), Decimal(0)),
            LedgerEntry("paper.held", "BTC", Decimal(0), Decimal("0.005")),
            LedgerEntry("paper.available", "USDT", Decimal("59.94"), Decimal(0)),
            LedgerEntry("paper.fee", "USDT", Decimal("0.06"), Decimal(0)),
            LedgerEntry("exchange.clearing", "USDT", Decimal(0), Decimal("60")),
        ),
    )
    valuation = Journal(
        stable_id(f"valuation-{fill_id}"),
        "paper.fill",
        fill_id,
        "VALUATION",
        (
            LedgerEntry("paper.disposal-value", "USDT_VAL", Decimal("60"), Decimal(0)),
            LedgerEntry("paper.realized-pnl", "USDT_VAL", Decimal(0), Decimal("9.89")),
            LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal(0), Decimal("50.05")),
            LedgerEntry("paper.fee-value", "USDT_VAL", Decimal(0), Decimal("0.06")),
        ),
    )
    filled = AtomicPaperWrite(
        initial.account_id,
        "test",
        None,
        None,
        (book,),
        (
            BalanceWrite("BTC", Decimal(0), Decimal(0), 4),
            BalanceWrite("USDT", Decimal("209.89"), Decimal(0), 3),
        ),
        replace(
            order,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("0.005"),
            held_amount=Decimal(0),
            version=2,
        ),
        (
            OrderEventWrite(
                stable_id(f"filled-{order_id}"),
                order_id,
                2,
                "paper.order.filled.v1",
                source_key,
                digest(fill_id),
                NOW,
            ),
        ),
        (fill,),
        (),
        (
            LotConsumption(
                stable_id(f"consume-{fill_id}"),
                consumption.lot_id,
                fill_id,
                Decimal("0.005"),
                Decimal("50.05"),
            ),
        ),
        (physical, valuation),
        (
            outbox(
                "paper.order.filled.v1",
                order_id,
                2,
                {"order_id": order_id, "fill_id": fill_id},
            ),
        ),
        NOW,
    )
    return opened, filled


def test_atomic_write_is_durable_idempotent_and_restart_stable() -> None:
    write = complete_write(suffix="restart")
    first = PostgresPaperStore(DATABASE_URL).commit(write)
    assert first.created
    restarted = PostgresPaperStore(DATABASE_URL)
    assert restarted.semantic_digest(write.account_id) == first.semantic_digest

    retry = restarted.commit(write)
    assert not retry.created
    assert retry.response == first.response
    assert retry.semantic_digest == first.semantic_digest

    checkpoint = restarted.reconcile(
        write.account_id, checkpoint_id="checkpoint-restart", created_at=NOW
    )
    assert checkpoint.status == "HEALTHY"
    assert checkpoint.mismatch_codes == ()


def test_restart_continues_existing_order_with_distinct_cancel_command() -> None:
    initial = complete_write(suffix="lifecycle")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    first = store.commit(initial)
    restarted = PostgresPaperStore(PAPER_WRITER_URL)
    hydrated = restarted.hydrate_engine(initial.account_id)
    assert hydrated.orders[initial.order.order_id] == initial.order
    cancelled = cancel_write(replace(initial, order=hydrated.orders[initial.order.order_id]))
    second = restarted.commit(cancelled)
    assert second.created
    assert second.semantic_digest != first.semantic_digest
    retry = PostgresPaperStore(PAPER_WRITER_URL).commit(cancelled)
    assert not retry.created
    assert retry.semantic_digest == second.semantic_digest
    rehydrated = PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(initial.account_id)
    assert rehydrated.orders[initial.order.order_id].status == OrderStatus.CANCELLED
    assert cancelled.receipt.idempotency_key in rehydrated.cancel_receipts
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,version,held_amount FROM paper_orders WHERE order_id=%s",
            (initial.order.order_id,),
        ).fetchone() == ("CANCELLED", 3, Decimal(0))


def test_restart_applies_and_idempotently_replays_observation_fill() -> None:
    opened, fill = split_open_and_fill(suffix="fill-lifecycle")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    first = store.commit(opened)
    hydrated = PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(opened.account_id)
    assert hydrated.orders[opened.order.order_id].status == OrderStatus.OPEN
    second = PostgresPaperStore(PAPER_WRITER_URL).commit(fill)
    assert second.created and second.semantic_digest != first.semantic_digest
    retry = PostgresPaperStore(PAPER_WRITER_URL).commit(fill)
    assert not retry.created and retry.semantic_digest == second.semantic_digest
    rehydrated = PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(opened.account_id)
    assert rehydrated.orders[opened.order.order_id].status == OrderStatus.PARTIALLY_FILLED
    assert len(rehydrated.fills) == 1


def test_restart_restores_floor_stepped_observation_budget_and_hash() -> None:
    write = complete_write(suffix="floor-budget")
    book = replace(
        write.broker_inputs[1],
        available_quantity=Decimal("0.100090000000000000"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "100.000000000000000000",
            "0.100090000000000000",
        ),
    )
    write = replace(write, broker_inputs=(write.broker_inputs[0], book))
    PostgresPaperStore(PAPER_WRITER_URL).commit(write)
    hydrated = PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(write.account_id)
    assert hydrated.observation_budgets[book.source_key] == (
        book.payload_hash,
        Decimal("0.005000000000000000"),
    )


def test_restart_completes_shared_observation_for_second_order_once() -> None:
    initial = complete_write(suffix="shared-observation")
    observation_seq = initial.order.accepted_broker_seq + 2
    initial = replace(
        initial,
        broker_inputs=(
            initial.broker_inputs[0],
            replace(initial.broker_inputs[1], broker_seq=observation_seq),
        ),
        fills=(replace(initial.fills[0], broker_seq=observation_seq),),
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    opened, filled = second_order_on_shared_observation(initial)
    store.commit(opened)
    first = PostgresPaperStore(PAPER_WRITER_URL).commit(filled)
    retry = PostgresPaperStore(PAPER_WRITER_URL).commit(filled)
    assert first.created is True
    assert retry.created is False
    restarted = store.hydrate_engine(initial.account_id)
    assert restarted.orders[filled.order.order_id].status == OrderStatus.FILLED
    assert restarted.observation_budgets[initial.broker_inputs[1].source_key][1] == 0
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*),sum(quantity) FROM paper_fills WHERE source_key=%s",
            (initial.broker_inputs[1].source_key,),
        ).fetchone() == (2, Decimal("0.010000000000000000"))
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE source_key=%s",
            (initial.broker_inputs[1].source_key,),
        ).fetchone() == (2,)

    blocked = complete_write(suffix="non-canonical-shared")
    blocked_seq = blocked.order.accepted_broker_seq + 2
    blocked = replace(
        blocked,
        broker_inputs=(
            blocked.broker_inputs[0],
            replace(blocked.broker_inputs[1], broker_seq=blocked_seq),
        ),
        fills=(replace(blocked.fills[0], broker_seq=blocked_seq),),
    )
    first_open = replace(
        blocked,
        broker_inputs=(blocked.broker_inputs[0],),
        balances=(BalanceWrite("USDT", Decimal("99.9"), Decimal("100.1"), 1),),
        order=replace(
            blocked.order,
            status=OrderStatus.OPEN,
            filled_quantity=Decimal(0),
            held_amount=Decimal("100.1"),
            version=1,
        ),
        order_events=(blocked.order_events[0],),
        fills=(),
        lots=(),
        journals=blocked.journals[:2],
        outbox=(blocked.outbox[0],),
    )
    younger_open, younger_fill = second_order_on_shared_observation(blocked)
    younger_open = replace(
        younger_open,
        balances=(BalanceWrite("USDT", Decimal("49.85"), Decimal("150.15"), 2),),
    )
    younger_fill = replace(
        younger_fill,
        balances=(
            BalanceWrite("USDT", Decimal("49.85"), Decimal("100.1"), 3),
            BalanceWrite("BTC", Decimal("0.005"), Decimal(0), 1),
        ),
    )
    store.commit(first_open)
    store.commit(younger_open)
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(younger_fill)


def test_sell_fill_binds_fifo_basis_and_exact_ledger_amounts() -> None:
    initial = complete_write(suffix="sell-exact")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    store.commit(cancel_write(initial))
    opened, filled = sell_order_after_cancel(initial)
    store.commit(opened)
    assert store.commit(filled).created is True
    restarted = store.hydrate_engine(initial.account_id)
    assert restarted.position("BTC") == 0
    assert restarted.realized_pnl("BTC") == Decimal("9.89")

    bad_initial = complete_write(suffix="sell-basis-mismatch")
    store.commit(bad_initial)
    store.commit(cancel_write(bad_initial))
    bad_opened, bad_fill = sell_order_after_cancel(bad_initial)
    store.commit(bad_opened)
    bad_consumption = replace(
        bad_fill.consumptions[0], quantity=Decimal("0.001"), quote_basis=Decimal("10.01")
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(replace(bad_fill, consumptions=(bad_consumption,)))


def test_concurrent_command_and_observation_retries_return_one_stored_effect() -> None:
    command = complete_write(suffix="concurrent-command")
    command_barrier = Barrier(2)

    def commit_command() -> CommitResult:
        command_barrier.wait()
        return PostgresPaperStore(PAPER_WRITER_URL).commit(command)

    with ThreadPoolExecutor(max_workers=2) as executor:
        command_results = tuple(executor.map(lambda _: commit_command(), range(2)))
    assert sorted(result.created for result in command_results) == [False, True]
    assert command_results[0].semantic_digest == command_results[1].semantic_digest

    opened, observation = split_open_and_fill(suffix="concurrent-observation")
    PostgresPaperStore(PAPER_WRITER_URL).commit(opened)
    observation_barrier = Barrier(2)

    def commit_observation() -> CommitResult:
        observation_barrier.wait()
        return PostgresPaperStore(PAPER_WRITER_URL).commit(observation)

    with ThreadPoolExecutor(max_workers=2) as executor:
        observation_results = tuple(executor.map(lambda _: commit_observation(), range(2)))
    assert sorted(result.created for result in observation_results) == [False, True]
    assert observation_results[0].semantic_digest == observation_results[1].semantic_digest


def test_rejected_command_is_durable_orderless_and_restart_idempotent() -> None:
    write = rejected_write(suffix="durable")
    first = PostgresPaperStore(PAPER_WRITER_URL).commit(write)
    assert first.created
    retry = PostgresPaperStore(PAPER_WRITER_URL).commit(write)
    assert not retry.created
    assert retry.response == first.response
    assert retry.semantic_digest == first.semantic_digest
    hydrated = PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(write.account_id)
    assert hydrated.orders == {}
    assert hydrated.command_receipts[write.receipt.idempotency_key].error_code == (
        "INSUFFICIENT_FUNDS"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_orders WHERE account_id=%s", (write.account_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_outbox_links WHERE account_id=%s", (write.account_id,)
        ).fetchone() == (2,)


def test_database_rejects_incomplete_financial_state_and_liquidity_overallocation() -> None:
    missing = replace(complete_write(suffix="missing-financial"), balances=(), journals=())
    with pytest.raises(psycopg.errors.RaiseException):
        PostgresPaperStore(DATABASE_URL).commit(missing)
    over = complete_write(suffix="overallocated")
    book = replace(
        over.broker_inputs[1],
        available_quantity=Decimal("0.04"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "100.000000000000000000",
            "0.040000000000000000",
        ),
    )
    over = replace(over, broker_inputs=(over.broker_inputs[0], book))
    with pytest.raises(psycopg.errors.RaiseException):
        PostgresPaperStore(DATABASE_URL).commit(over)
    ineligible = complete_write(suffix="ineligible-book")
    bad_book = replace(
        ineligible.broker_inputs[1],
        best_ask=Decimal("10001"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "10001.000000000000000000",
            "0.100000000000000000",
        ),
    )
    ineligible = replace(
        ineligible,
        broker_inputs=(ineligible.broker_inputs[0], bad_book),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(ineligible)
    cross_symbol = complete_write(suffix="cross-symbol-book")
    cross_book = replace(
        cross_symbol.broker_inputs[1],
        symbol="ETHUSDT",
        payload_hash=engine_id(
            "observation",
            "ETHUSDT",
            "99.000000000000000000",
            "100.000000000000000000",
            "0.100000000000000000",
        ),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(
            replace(
                cross_symbol,
                broker_inputs=(cross_symbol.broker_inputs[0], cross_book),
            )
        )
    bad_fee_asset = complete_write(suffix="bad-fee-asset")
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(
            replace(bad_fee_asset, fills=(replace(bad_fee_asset.fills[0], fee_asset="BTC"),))
        )
    invalid_open, _ = split_open_and_fill(suffix="invalid-unfilled-order")
    invalid_open = replace(
        invalid_open,
        order=replace(invalid_open.order, limit_price=Decimal("10000.001")),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(invalid_open)
    mismatched_lot = complete_write(suffix="mismatched-lot")
    lot = replace(
        mismatched_lot.lots[0],
        acquired_quantity=Decimal("0.001"),
        quote_cost=Decimal("10.01"),
    )
    mismatched_lot = replace(mismatched_lot, lots=(lot,))
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(mismatched_lot)


def test_failed_reconciliation_checkpoint_holds_new_lifecycle_command() -> None:
    initial = complete_write(suffix="reconciliation-hold")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "INSERT INTO paper_reconciliation_checkpoints"
            "(checkpoint_id,account_id,input_digest,output_digest,status,mismatch_codes,created_at) "
            "VALUES (%s,%s,%s,%s,'FAILED','[\"TEST_MISMATCH\"]'::jsonb,%s)",
            (
                "forced-failed-checkpoint",
                initial.account_id,
                digest("failed-input"),
                digest("failed-output"),
                NOW,
            ),
        )
    with pytest.raises(RuntimeError, match="PAPER_RECONCILIATION_HOLD"):
        PostgresPaperStore(PAPER_WRITER_URL).commit(cancel_write(initial))


def test_database_rejects_orphan_relations_fill_mismatch_empty_journal_and_bad_correction() -> None:
    with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.ForeignKeyViolation)):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO paper_accounts(account_id,namespace,created_at)
                VALUES ('orphan-account','test',%s)
                """,
                (NOW,),
            )
            connection.execute(
                """
                INSERT INTO paper_authorization_attempts
                    (authorization_id,authorization_nonce,account_id,command_scope,
                     idempotency_key,namespace,request_hash,outcome,reason_code,created_at)
                VALUES ('orphan-auth','orphan-nonce','orphan-account','paper.create.v1',
                        'missing','test',%s,'BLOCKED','TEST_BLOCK',%s)
                """,
                (digest("orphan"), NOW),
            )

    write = complete_write(suffix="constraints")
    PostgresPaperStore(DATABASE_URL).commit(write)
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE paper_orders SET filled_quantity=0 WHERE order_id=%s",
                (write.order.order_id,),
            )

    with pytest.raises(psycopg.errors.RaiseException, match="at least one entry"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO paper_ledger_transactions
                    (transaction_id,account_id,business_event_type,business_event_id,
                     journal_kind,posted_at)
                VALUES ('empty-journal',%s,'paper.test','empty','PHYSICAL',%s)
                """,
                (write.account_id, NOW),
            )

    with pytest.raises(psycopg.errors.RaiseException, match="correction pair"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO paper_ledger_transactions
                    (transaction_id,account_id,business_event_type,business_event_id,
                     journal_kind,reversal_of,posted_at)
                VALUES ('unpaired-reversal',%s,'paper.correction','correction-x',
                        'PHYSICAL',%s,%s)
                """,
                (write.account_id, write.journals[0].journal_id, NOW),
            )
            connection.execute(
                """
                INSERT INTO paper_ledger_entries
                    (transaction_id,line_no,account_code,commodity,debit,credit)
                SELECT 'unpaired-reversal',line_no,account_code,commodity,credit,debit
                FROM paper_ledger_entries WHERE transaction_id=%s
                """,
                (write.journals[0].journal_id,),
            )

    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO paper_lot_consumptions"
                "(consumption_id,lot_id,source_fill_id,quantity,quote_basis) "
                "VALUES (%s,%s,%s,0.006,60)",
                (stable_id("overconsume"), write.lots[0].lot_id, write.fills[0].fill_id),
            )


def test_writer_commit_succeeds_and_unledgered_balance_update_is_rejected() -> None:
    write = complete_write(suffix="reconcile")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    assert store.commit(write).created
    with pytest.raises(psycopg.errors.RaiseException, match="balance/ledger authority"):
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                """
                UPDATE paper_asset_balances SET available=available+1,version=version+1
                WHERE account_id=%s AND asset='USDT'
                """,
                (write.account_id,),
            )
    healthy = store.reconcile(write.account_id, checkpoint_id="checkpoint-writer", created_at=NOW)
    assert healthy.status == "HEALTHY"
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,mismatch_codes FROM paper_reconciliation_checkpoints "
            "WHERE checkpoint_id='checkpoint-writer'"
        ).fetchone() == ("HEALTHY", [])
        update_columns = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name,column_name FROM information_schema.column_privileges
                WHERE grantee='woozoo_paper_engine' AND privilege_type='UPDATE'
                  AND table_schema='public'
                """
            ).fetchall()
        }
    assert update_columns == {
        ("paper_asset_balances", "available"),
        ("paper_asset_balances", "held"),
        ("paper_asset_balances", "version"),
        ("paper_orders", "filled_quantity"),
        ("paper_orders", "held_amount"),
        ("paper_orders", "status"),
        ("paper_orders", "version"),
    }
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "UPDATE paper_orders SET client_order_id='forbidden' WHERE order_id=%s",
                (write.order.order_id,),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(EVIDENCE_WRITER_URL) as connection:
            connection.execute(
                "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (
                    "f" * 64,
                    "paper.order.accepted.v1",
                    "{}",
                    "e" * 64,
                    NOW,
                    "paper_order",
                    "d" * 64,
                    1,
                ),
            )
    with pytest.raises(psycopg.errors.RaiseException, match="closed Paper outbox"):
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (
                    "f" * 64,
                    "paper.forged.v1",
                    json.dumps({"event_id": "f" * 64}),
                    "e" * 64,
                    NOW,
                    "paper_order",
                    "d" * 64,
                    1,
                ),
            )
    existing = write.outbox[0]
    extra_payload = {**existing.payload, "unexpected": "field"}
    with pytest.raises(psycopg.errors.RaiseException, match="closed Paper outbox"):
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (
                    existing.event_id,
                    existing.event_type,
                    json.dumps(extra_payload),
                    existing.payload_hash,
                    existing.occurred_at,
                    existing.aggregate_type,
                    existing.aggregate_id,
                    existing.aggregate_version,
                ),
            )
    unlinked = outbox(
        "ledger.transaction.posted.v1",
        "c" * 64,
        1,
        {"transaction_id": "c" * 64},
    )
    with pytest.raises(psycopg.errors.RaiseException, match="Unlinked Paper outbox"):
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (
                    unlinked.event_id,
                    unlinked.event_type,
                    json.dumps(unlinked.payload),
                    unlinked.payload_hash,
                    unlinked.occurred_at,
                    unlinked.aggregate_type,
                    unlinked.aggregate_id,
                    unlinked.aggregate_version,
                ),
            )


def test_downgrade_preserves_a_preexisting_writer_role() -> None:
    run(sys.executable, "-m", "alembic", "downgrade", "20260719_0003")
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute("DROP ROLE IF EXISTS woozoo_paper_engine")
        connection.execute("CREATE ROLE woozoo_paper_engine LOGIN")
    try:
        run(sys.executable, "-m", "alembic", "upgrade", "head")
        run(sys.executable, "-m", "alembic", "downgrade", "20260719_0003")
        with psycopg.connect(DATABASE_URL) as connection:
            assert connection.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname='woozoo_paper_engine'"
            ).fetchone() == (1,)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            connection.execute("DROP ROLE IF EXISTS woozoo_paper_engine")
        run(sys.executable, "-m", "alembic", "upgrade", "head")


def test_downgrade_removes_a_migration_created_writer_role() -> None:
    run(sys.executable, "-m", "alembic", "downgrade", "20260719_0003")
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute("DROP ROLE IF EXISTS woozoo_paper_engine")
    try:
        run(sys.executable, "-m", "alembic", "upgrade", "head")
        run(sys.executable, "-m", "alembic", "downgrade", "20260719_0003")
        with psycopg.connect(DATABASE_URL) as connection:
            assert connection.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname='woozoo_paper_engine'"
            ).fetchone() == (0,)
    finally:
        run(sys.executable, "-m", "alembic", "upgrade", "head")
