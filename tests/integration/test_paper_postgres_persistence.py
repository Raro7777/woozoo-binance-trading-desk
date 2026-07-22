from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb
import pytest

from docker_infrastructure_lock import docker_infrastructure_lock
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


@pytest.fixture(scope="module", autouse=True)
def postgres() -> Iterator[None]:
    with docker_infrastructure_lock():
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
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
        "0.050000000000000000",
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
            journal_id=stable_id(f"seed-journal-{suffix}"),
            business_event_type="paper.seed",
            business_event_id=f"seed-{suffix}",
            journal_kind="PHYSICAL",
            entries=(
                LedgerEntry("paper.available", "USDT", Decimal("200"), Decimal(0)),
                LedgerEntry("paper.opening-equity", "USDT", Decimal(0), Decimal("200")),
            ),
        ),
        Journal(
            journal_id=stable_id(f"hold-journal-{suffix}"),
            business_event_type="paper.hold",
            business_event_id=order_id,
            journal_kind="PHYSICAL",
            entries=(
                LedgerEntry("paper.held", "USDT", Decimal("100.1"), Decimal(0)),
                LedgerEntry("paper.available", "USDT", Decimal(0), Decimal("100.1")),
            ),
        ),
        Journal(
            journal_id=stable_id(f"fill-journal-{suffix}"),
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
            journal_id=stable_id(f"valuation-journal-{suffix}"),
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
                Decimal("0.050000000000000000"),
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
        source.broker_seq,
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
    with psycopg.connect(DATABASE_URL) as connection:
        committed_effect_counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM paper_command_receipts WHERE account_id=%s),
              (SELECT count(*) FROM paper_orders WHERE account_id=%s),
              (SELECT count(*) FROM paper_fills fill
                 JOIN paper_orders paper_order USING(order_id)
                WHERE paper_order.account_id=%s),
              (SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),
              (SELECT count(*) FROM paper_order_events event
                 JOIN paper_orders paper_order USING(order_id)
                WHERE paper_order.account_id=%s),
              (SELECT count(*) FROM paper_outbox_links WHERE account_id=%s)
            """,
            (write.account_id,) * 6,
        ).fetchone()
    assert committed_effect_counts == (1, 1, 1, 4, 2, 2)

    # Treat the successful commit response as lost. A newly constructed
    # repository is the process boundary for the retry and may use only DB state.
    restarted = PostgresPaperStore(DATABASE_URL)
    assert restarted.semantic_digest(write.account_id) == first.semantic_digest

    retry = restarted.commit(write)
    assert not retry.created
    assert retry.response == first.response
    assert retry.semantic_digest == first.semantic_digest
    with psycopg.connect(DATABASE_URL) as connection:
        replayed_effect_counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM paper_command_receipts WHERE account_id=%s),
              (SELECT count(*) FROM paper_orders WHERE account_id=%s),
              (SELECT count(*) FROM paper_fills fill
                 JOIN paper_orders paper_order USING(order_id)
                WHERE paper_order.account_id=%s),
              (SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),
              (SELECT count(*) FROM paper_order_events event
                 JOIN paper_orders paper_order USING(order_id)
                WHERE paper_order.account_id=%s),
              (SELECT count(*) FROM paper_outbox_links WHERE account_id=%s)
            """,
            (write.account_id,) * 6,
        ).fetchone()
    assert replayed_effect_counts == committed_effect_counts

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
        available_quantity=Decimal("0.050090000000000000"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "100.000000000000000000",
            "0.050090000000000000",
        ),
    )
    write = replace(write, broker_inputs=(write.broker_inputs[0], book))
    PostgresPaperStore(PAPER_WRITER_URL).commit(write)
    hydrated = PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(write.account_id)
    assert hydrated.observation_budgets[book.source_key] == (
        book.payload_hash,
        Decimal("0.000000000000000000"),
    )


@pytest.mark.parametrize(
    ("label", "available_quantity", "expected_fill_quantity"),
    [
        ("below", Decimal("0.000099999999999999"), Decimal(0)),
        ("exact", Decimal("0.000100000000000000"), Decimal("0.00001")),
        ("above", Decimal("0.000100000000000001"), Decimal("0.00001")),
    ],
)
def test_postgres_and_hydration_share_exact_participation_floor_boundaries(
    label: str,
    available_quantity: Decimal,
    expected_fill_quantity: Decimal,
) -> None:
    opened, candidate = split_open_and_fill(suffix=f"floor-boundary-{label}")
    source = candidate.broker_inputs[0]
    book = replace(
        source,
        available_quantity=available_quantity,
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "100.000000000000000000",
            format(available_quantity, "f"),
        ),
    )
    if expected_fill_quantity == 0:
        effect = replace(
            candidate,
            broker_inputs=(book,),
            balances=(),
            order=opened.order,
            order_events=(),
            fills=(),
            lots=(),
            journals=(),
            outbox=(),
        )
    else:
        debit = Decimal("0.1001")
        fill = replace(
            candidate.fills[0],
            quantity=expected_fill_quantity,
            fee_amount=Decimal("0.0001"),
        )
        effect = replace(
            candidate,
            broker_inputs=(book,),
            balances=(
                BalanceWrite("USDT", Decimal("99.9"), Decimal("99.9999"), 2),
                BalanceWrite("BTC", expected_fill_quantity, Decimal(0), 1),
            ),
            order=replace(
                candidate.order,
                status=OrderStatus.PARTIALLY_FILLED,
                filled_quantity=expected_fill_quantity,
                held_amount=Decimal("99.9999"),
            ),
            fills=(fill,),
            lots=(
                replace(
                    candidate.lots[0],
                    acquired_quantity=expected_fill_quantity,
                    quote_cost=debit,
                ),
            ),
            journals=(
                replace(
                    candidate.journals[0],
                    entries=(
                        LedgerEntry("paper.asset", "BTC", expected_fill_quantity, Decimal(0)),
                        LedgerEntry(
                            "exchange.clearing",
                            "BTC",
                            Decimal(0),
                            expected_fill_quantity,
                        ),
                        LedgerEntry("exchange.clearing", "USDT", Decimal("0.1"), Decimal(0)),
                        LedgerEntry("paper.fee", "USDT", Decimal("0.0001"), Decimal(0)),
                        LedgerEntry("paper.held", "USDT", Decimal(0), debit),
                    ),
                ),
                replace(
                    candidate.journals[1],
                    entries=(
                        LedgerEntry("paper.inventory-basis", "USDT_VAL", debit, Decimal(0)),
                        LedgerEntry("paper.acquisition-value", "USDT_VAL", Decimal(0), debit),
                    ),
                ),
            ),
        )

    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(opened)
    assert store.commit(effect).created is True
    restarted = store.hydrate_engine(opened.account_id)
    assert restarted.observation_budgets[book.source_key][1] == Decimal(0)
    assert (
        sum(
            fill.quantity
            for fill in restarted.fills.values()
            if fill.observation_id == book.source_key
        )
        == expected_fill_quantity
    )
    assert store.hydrate_engine(opened.account_id).semantic_digest() == restarted.semantic_digest()


def test_observation_no_fill_requires_ineligibility_or_exhausted_budget() -> None:
    opened, eligible_fill = split_open_and_fill(suffix="eligible-no-fill")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(opened)
    eligible_no_fill = replace(
        eligible_fill,
        balances=(),
        order=opened.order,
        order_events=(),
        fills=(),
        lots=(),
        journals=(),
        outbox=(),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(eligible_no_fill)

    terminal = complete_write(suffix="terminal-no-fill")
    store.commit(terminal)
    terminal_cancelled = cancel_write(terminal)
    store.commit(terminal_cancelled)
    terminal_source = replace(
        terminal.broker_inputs[1],
        broker_seq=terminal_cancelled.receipt.broker_seq + 1,
        source_key="book:terminal-no-fill:after-filled",
    )
    terminal_no_fill = replace(
        terminal_cancelled,
        receipt=None,
        authorization_attempt=None,
        broker_inputs=(terminal_source,),
        balances=(),
        order_events=(),
        fills=(),
        lots=(),
        journals=(),
        outbox=(),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(terminal_no_fill)

    ineligible_open, ineligible_fill = split_open_and_fill(suffix="ineligible-no-fill")
    store.commit(ineligible_open)
    source = ineligible_fill.broker_inputs[0]
    ineligible_source = replace(
        source,
        best_ask=Decimal("10001"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "10001.000000000000000000",
            "0.050000000000000000",
        ),
    )
    ineligible_no_fill = replace(
        ineligible_fill,
        broker_inputs=(ineligible_source,),
        balances=(),
        order=ineligible_open.order,
        order_events=(),
        fills=(),
        lots=(),
        journals=(),
        outbox=(),
    )
    assert store.commit(ineligible_no_fill).created is True
    restarted = store.hydrate_engine(ineligible_open.account_id)
    assert (ineligible_open.order.order_id, ineligible_source.source_key) in (
        restarted.observation_effects
    )
    cancelled = cancel_write(ineligible_open)
    cancelled = replace(
        cancelled,
        balances=(BalanceWrite("USDT", Decimal("200"), Decimal(0), 2),),
        order=replace(cancelled.order, version=2),
        order_events=(replace(cancelled.order_events[0], order_version=2),),
        journals=(
            replace(
                cancelled.journals[0],
                entries=(
                    LedgerEntry("paper.available", "USDT", Decimal("100.1"), Decimal(0)),
                    LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("100.1")),
                ),
            ),
        ),
        outbox=(
            outbox(
                "paper.order.cancelled.v1",
                cancelled.order.order_id,
                2,
                {
                    "order_id": cancelled.order.order_id,
                    "cancel_id": cancelled.receipt.idempotency_key,
                },
            ),
        ),
    )
    assert store.commit(cancelled).created is True

    later_open, later_fill = split_open_and_fill(suffix="ineligible-then-fill")
    store.commit(later_open)
    later_source = later_fill.broker_inputs[0]
    later_ineligible_source = replace(
        later_source,
        best_ask=Decimal("10001"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "10001.000000000000000000",
            "0.050000000000000000",
        ),
    )
    store.commit(
        replace(
            later_fill,
            broker_inputs=(later_ineligible_source,),
            balances=(),
            order=later_open.order,
            order_events=(),
            fills=(),
            lots=(),
            journals=(),
            outbox=(),
        )
    )
    eligible_after_no_fill = replace(
        later_fill.broker_inputs[0],
        broker_seq=later_source.broker_seq + 1,
        source_key="book:ineligible-then-fill:eligible",
    )
    later_fill = replace(
        later_fill,
        broker_inputs=(eligible_after_no_fill,),
        order_events=(
            replace(later_fill.order_events[0], source_key=eligible_after_no_fill.source_key),
        ),
        fills=(
            replace(
                later_fill.fills[0],
                observation_id=eligible_after_no_fill.source_key,
                broker_seq=eligible_after_no_fill.broker_seq,
            ),
        ),
    )
    assert store.commit(later_fill).created is True


def test_restart_completes_shared_observation_for_second_order_once() -> None:
    initial = complete_write(suffix="shared-observation")
    observation_seq = initial.order.accepted_broker_seq + 2
    shared_book = replace(
        initial.broker_inputs[1],
        broker_seq=observation_seq,
        available_quantity=Decimal("0.1"),
        payload_hash=engine_id(
            "observation",
            "BTCUSDT",
            "99.000000000000000000",
            "100.000000000000000000",
            "0.100000000000000000",
        ),
    )
    initial = replace(
        initial,
        broker_inputs=(
            initial.broker_inputs[0],
            shared_book,
        ),
        balances=(
            BalanceWrite("USDT", Decimal("169.97"), Decimal(0), 1),
            BalanceWrite("BTC", Decimal("0.003"), Decimal(0), 1),
        ),
        order=replace(
            initial.order,
            quantity=Decimal("0.003"),
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("0.003"),
            held_amount=Decimal(0),
        ),
        order_events=(
            initial.order_events[0],
            replace(initial.order_events[1], event_type="paper.order.filled.v1"),
        ),
        fills=(
            replace(
                initial.fills[0],
                broker_seq=observation_seq,
                quantity=Decimal("0.003"),
                fee_amount=Decimal("0.03"),
            ),
        ),
        lots=(
            replace(
                initial.lots[0],
                acquired_quantity=Decimal("0.003"),
                quote_cost=Decimal("30.03"),
            ),
        ),
        journals=(
            initial.journals[0],
            replace(
                initial.journals[1],
                entries=(
                    LedgerEntry("paper.held", "USDT", Decimal("30.03"), Decimal(0)),
                    LedgerEntry("paper.available", "USDT", Decimal(0), Decimal("30.03")),
                ),
            ),
            replace(
                initial.journals[2],
                entries=(
                    LedgerEntry("paper.asset", "BTC", Decimal("0.003"), Decimal(0)),
                    LedgerEntry("exchange.clearing", "BTC", Decimal(0), Decimal("0.003")),
                    LedgerEntry("exchange.clearing", "USDT", Decimal("30"), Decimal(0)),
                    LedgerEntry("paper.fee", "USDT", Decimal("0.03"), Decimal(0)),
                    LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("30.03")),
                ),
            ),
            replace(
                initial.journals[3],
                entries=(
                    LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal("30.03"), Decimal(0)),
                    LedgerEntry(
                        "paper.acquisition-value", "USDT_VAL", Decimal(0), Decimal("30.03")
                    ),
                ),
            ),
        ),
        outbox=(
            initial.outbox[0],
            outbox(
                "paper.order.filled.v1",
                initial.order.order_id,
                2,
                {"order_id": initial.order.order_id, "fill_id": initial.fills[0].fill_id},
            ),
        ),
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    opened, filled = second_order_on_shared_observation(initial)
    opened = replace(
        opened,
        balances=(BalanceWrite("USDT", Decimal("69.87"), Decimal("100.1"), 2),),
        order=replace(opened.order, quantity=Decimal("0.01"), held_amount=Decimal("100.1")),
        journals=(
            replace(
                opened.journals[0],
                entries=(
                    LedgerEntry("paper.held", "USDT", Decimal("100.1"), Decimal(0)),
                    LedgerEntry("paper.available", "USDT", Decimal(0), Decimal("100.1")),
                ),
            ),
        ),
    )
    filled = replace(
        filled,
        balances=(
            BalanceWrite("USDT", Decimal("69.87"), Decimal("30.03"), 3),
            BalanceWrite("BTC", Decimal("0.01"), Decimal(0), 2),
        ),
        order=replace(
            filled.order,
            quantity=Decimal("0.01"),
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=Decimal("0.007"),
            held_amount=Decimal("30.03"),
        ),
        order_events=(
            replace(filled.order_events[0], event_type="paper.order.partially-filled.v1"),
        ),
        fills=(replace(filled.fills[0], quantity=Decimal("0.007"), fee_amount=Decimal("0.07")),),
        lots=(
            replace(
                filled.lots[0],
                acquired_quantity=Decimal("0.007"),
                quote_cost=Decimal("70.07"),
            ),
        ),
        journals=(
            replace(
                filled.journals[0],
                entries=(
                    LedgerEntry("paper.asset", "BTC", Decimal("0.007"), Decimal(0)),
                    LedgerEntry("exchange.clearing", "BTC", Decimal(0), Decimal("0.007")),
                    LedgerEntry("exchange.clearing", "USDT", Decimal("70"), Decimal(0)),
                    LedgerEntry("paper.fee", "USDT", Decimal("0.07"), Decimal(0)),
                    LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("70.07")),
                ),
            ),
            replace(
                filled.journals[1],
                entries=(
                    LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal("70.07"), Decimal(0)),
                    LedgerEntry(
                        "paper.acquisition-value", "USDT_VAL", Decimal(0), Decimal("70.07")
                    ),
                ),
            ),
        ),
        outbox=(
            outbox(
                "paper.order.partially-filled.v1",
                filled.order.order_id,
                2,
                {"order_id": filled.order.order_id, "fill_id": filled.fills[0].fill_id},
            ),
        ),
    )
    store.commit(opened)
    hydrated_before_fill = store.hydrate_engine(initial.account_id)
    actual_fill = hydrated_before_fill.apply_book_observation(
        order_id=opened.order.order_id,
        observation_id=shared_book.source_key,
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="0.1",
    )
    assert actual_fill is not None
    assert actual_fill.broker_seq == observation_seq
    assert actual_fill.fill_id < initial.fills[0].fill_id
    assert (initial.fills[0].quantity, actual_fill.quantity) == (
        Decimal("0.003"),
        Decimal("0.007"),
    )
    actual_order = hydrated_before_fill.orders[opened.order.order_id]
    actual_lot = next(
        lot for lot in hydrated_before_fill.lots if lot.source_fill_id == actual_fill.fill_id
    )
    actual_journals = tuple(
        journal
        for journal in hydrated_before_fill.journals.values()
        if journal.business_event_id == actual_fill.fill_id
    )
    filled = replace(
        filled,
        created_at=actual_lot.acquired_at,
        balances=(
            BalanceWrite(
                "USDT",
                hydrated_before_fill.available["USDT"],
                hydrated_before_fill.held["USDT"],
                3,
            ),
            BalanceWrite("BTC", hydrated_before_fill.available["BTC"], Decimal(0), 2),
        ),
        order=actual_order,
        order_events=(
            replace(
                filled.order_events[0],
                event_type="paper.order.partially-filled.v1",
                payload_hash=digest(actual_fill.fill_id),
            ),
        ),
        fills=(actual_fill,),
        lots=(actual_lot,),
        journals=actual_journals,
        outbox=(
            outbox(
                "paper.order.partially-filled.v1",
                actual_order.order_id,
                actual_order.version,
                {"order_id": actual_order.order_id, "fill_id": actual_fill.fill_id},
            ),
        ),
    )
    first = PostgresPaperStore(PAPER_WRITER_URL).commit(filled)
    retry = PostgresPaperStore(PAPER_WRITER_URL).commit(filled)
    assert first.created is True
    assert retry.created is False
    restarted = store.hydrate_engine(initial.account_id)
    assert restarted.orders[filled.order.order_id].status == OrderStatus.PARTIALLY_FILLED
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


def test_hydrated_engine_and_database_reject_ineligible_later_order_before_eligible_first() -> None:
    template = complete_write(suffix="mixed-canonical-db")
    first_open, _ = split_open_and_fill(suffix="mixed-canonical-db")
    later_open, _ = second_order_on_shared_observation(template)
    later_hold = Decimal("45.045")
    later_open = replace(
        later_open,
        balances=(BalanceWrite("USDT", Decimal("54.855"), Decimal("145.145"), 2),),
        order=replace(later_open.order, limit_price=Decimal("9000"), held_amount=later_hold),
        journals=(
            replace(
                later_open.journals[0],
                entries=(
                    LedgerEntry("paper.held", "USDT", later_hold, Decimal(0)),
                    LedgerEntry("paper.available", "USDT", Decimal(0), later_hold),
                ),
            ),
        ),
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(first_open)
    store.commit(later_open)
    before = store.semantic_digest(template.account_id)

    hydrated = store.hydrate_engine(template.account_id)
    engine_before = hydrated.semantic_digest()
    with pytest.raises(ValueError, match="NON_CANONICAL_OBSERVATION_ORDER"):
        hydrated.apply_book_observation(
            order_id=later_open.order.order_id,
            observation_id="book:mixed-canonical-db",
            best_bid_text="9499",
            best_ask_text="9500",
            displayed_quantity_text="0.1",
        )
    assert hydrated.semantic_digest() == engine_before

    observation = BrokerInputWrite(
        later_open.order.accepted_broker_seq + 1,
        template.account_id,
        "RECORDED_BOOK",
        "book:mixed-canonical-db",
        engine_id(
            "observation",
            "BTCUSDT",
            "9499.000000000000000000",
            "9500.000000000000000000",
            "0.100000000000000000",
        ),
        NOW,
        Decimal("0.1"),
        "BTCUSDT",
        Decimal("9499"),
        Decimal("9500"),
    )
    no_fill = replace(
        later_open,
        receipt=None,
        authorization_attempt=None,
        broker_inputs=(observation,),
        balances=(),
        order_events=(),
        fills=(),
        lots=(),
        consumptions=(),
        journals=(),
        outbox=(),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(no_fill)
    assert store.semantic_digest(template.account_id) == before


def test_hydrated_engine_ignores_observation_older_than_order_acceptance() -> None:
    initial = complete_write(suffix="hydrated-old-observation")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    opened, _ = second_order_on_shared_observation(initial)
    command_seq = initial.broker_inputs[1].broker_seq + 1
    opened = replace(
        opened,
        receipt=replace(opened.receipt, broker_seq=command_seq),
        broker_inputs=(replace(opened.broker_inputs[0], broker_seq=command_seq),),
        balances=(BalanceWrite("USDT", Decimal("49.85"), Decimal("100.1"), 2),),
        order=replace(opened.order, accepted_broker_seq=command_seq),
    )
    store.commit(opened)

    hydrated = store.hydrate_engine(initial.account_id)
    prior_budget = hydrated.observation_budgets[initial.broker_inputs[1].source_key]
    prior_seq = hydrated.broker_seq
    assert (
        hydrated.apply_book_observation(
            order_id=opened.order.order_id,
            observation_id=initial.broker_inputs[1].source_key,
            best_bid_text="99",
            best_ask_text="100",
            displayed_quantity_text="0.05",
        )
        is None
    )
    assert (
        opened.order.order_id,
        initial.broker_inputs[1].source_key,
    ) not in hydrated.observation_effects
    assert hydrated.observation_budgets[initial.broker_inputs[1].source_key] == prior_budget
    assert hydrated.broker_seq == prior_seq


def test_extreme_sell_numeric_overflow_rolls_back_the_entire_database_write() -> None:
    initial = complete_write(suffix="sell-numeric-overflow")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    store.commit(cancel_write(initial))
    opened, filled = sell_order_after_cancel(initial)
    store.commit(opened)
    before = store.semantic_digest(initial.account_id)
    overflow = replace(
        filled,
        balances=(
            filled.balances[0],
            replace(filled.balances[1], available=Decimal("100000000000000000000")),
        ),
    )

    with pytest.raises(psycopg.errors.NumericValueOutOfRange):
        store.commit(overflow)

    assert store.semantic_digest(initial.account_id) == before
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_fills WHERE fill_id=%s",
            (filled.fills[0].fill_id,),
        ).fetchone() == (0,)


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
    bad_opened = replace(
        bad_opened,
        balances=(BalanceWrite("BTC", Decimal("0.004"), Decimal("0.001"), 3),),
        order=replace(bad_opened.order, quantity=Decimal("0.001"), held_amount=Decimal("0.001")),
        journals=(
            replace(
                bad_opened.journals[0],
                entries=(
                    LedgerEntry("paper.held", "BTC", Decimal("0.001"), Decimal(0)),
                    LedgerEntry("paper.available", "BTC", Decimal(0), Decimal("0.001")),
                ),
            ),
        ),
    )
    store.commit(bad_opened)
    bad_fill = replace(
        bad_fill,
        balances=(
            BalanceWrite("BTC", Decimal("0.004"), Decimal(0), 4),
            BalanceWrite("USDT", Decimal("161.938"), Decimal(0), 3),
        ),
        order=replace(
            bad_fill.order,
            quantity=Decimal("0.001"),
            filled_quantity=Decimal("0.001"),
        ),
        fills=(
            replace(
                bad_fill.fills[0],
                quantity=Decimal("0.001"),
                fee_amount=Decimal("0.012"),
            ),
        ),
        consumptions=(
            replace(
                bad_fill.consumptions[0],
                quantity=Decimal("0.001"),
                quote_basis=Decimal("1"),
            ),
        ),
        journals=(
            replace(
                bad_fill.journals[0],
                entries=(
                    LedgerEntry("exchange.clearing", "BTC", Decimal("0.001"), Decimal(0)),
                    LedgerEntry("paper.held", "BTC", Decimal(0), Decimal("0.001")),
                    LedgerEntry("paper.available", "USDT", Decimal("11.988"), Decimal(0)),
                    LedgerEntry("paper.fee", "USDT", Decimal("0.012"), Decimal(0)),
                    LedgerEntry("exchange.clearing", "USDT", Decimal(0), Decimal("12")),
                ),
            ),
            replace(
                bad_fill.journals[1],
                entries=(
                    LedgerEntry("paper.disposal-value", "USDT_VAL", Decimal("12"), Decimal(0)),
                    LedgerEntry("paper.realized-pnl", "USDT_VAL", Decimal(0), Decimal("10.988")),
                    LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal(0), Decimal("1")),
                    LedgerEntry("paper.fee-value", "USDT_VAL", Decimal(0), Decimal("0.012")),
                ),
            ),
        ),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(bad_fill)


def test_shared_sell_observation_uses_canonical_sale_order_for_fifo_basis() -> None:
    initial = complete_write(suffix="shared-sell-fifo")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    store.commit(cancel_write(initial))

    second_buy_open, second_buy_fill = second_order_on_shared_observation(initial)
    second_buy_command_seq = initial.order.accepted_broker_seq + 3
    second_buy_open = replace(
        second_buy_open,
        receipt=replace(second_buy_open.receipt, broker_seq=second_buy_command_seq),
        broker_inputs=(
            replace(second_buy_open.broker_inputs[0], broker_seq=second_buy_command_seq),
        ),
        balances=(BalanceWrite("USDT", Decimal("94.895"), Decimal("55.055"), 3),),
        order=replace(
            second_buy_open.order,
            limit_price=Decimal("11000"),
            held_amount=Decimal("55.055"),
            accepted_broker_seq=second_buy_command_seq,
        ),
        journals=(
            replace(
                second_buy_open.journals[0],
                entries=(
                    LedgerEntry("paper.held", "USDT", Decimal("55.055"), Decimal(0)),
                    LedgerEntry("paper.available", "USDT", Decimal(0), Decimal("55.055")),
                ),
            ),
        ),
    )
    second_buy_book_seq = second_buy_command_seq + 1
    second_buy_source = replace(
        second_buy_fill.broker_inputs[0],
        broker_seq=second_buy_book_seq,
        source_key="book:shared-sell-second-buy",
    )
    second_buy_fill = replace(
        second_buy_fill,
        broker_inputs=(second_buy_source,),
        balances=(
            BalanceWrite("USDT", Decimal("94.895"), Decimal(0), 4),
            BalanceWrite("BTC", Decimal("0.01"), Decimal(0), 3),
        ),
        order=replace(
            second_buy_fill.order,
            limit_price=Decimal("11000"),
            accepted_broker_seq=second_buy_command_seq,
        ),
        order_events=(
            replace(second_buy_fill.order_events[0], source_key=second_buy_source.source_key),
        ),
        fills=(
            replace(
                second_buy_fill.fills[0],
                observation_id=second_buy_source.source_key,
                broker_seq=second_buy_book_seq,
                price=Decimal("11000"),
                fee_amount=Decimal("0.055"),
            ),
        ),
        lots=(replace(second_buy_fill.lots[0], quote_cost=Decimal("55.055")),),
        journals=(
            replace(
                second_buy_fill.journals[0],
                entries=(
                    LedgerEntry("paper.asset", "BTC", Decimal("0.005"), Decimal(0)),
                    LedgerEntry("exchange.clearing", "BTC", Decimal(0), Decimal("0.005")),
                    LedgerEntry("exchange.clearing", "USDT", Decimal("55"), Decimal(0)),
                    LedgerEntry("paper.fee", "USDT", Decimal("0.055"), Decimal(0)),
                    LedgerEntry("paper.held", "USDT", Decimal(0), Decimal("55.055")),
                ),
            ),
            replace(
                second_buy_fill.journals[1],
                entries=(
                    LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal("55.055"), Decimal(0)),
                    LedgerEntry(
                        "paper.acquisition-value", "USDT_VAL", Decimal(0), Decimal("55.055")
                    ),
                ),
            ),
        ),
    )
    store.commit(second_buy_open)
    store.commit(second_buy_fill)

    first_open, _ = sell_order_after_cancel(initial)
    first_command_seq = second_buy_book_seq + 1
    first_open = replace(
        first_open,
        receipt=replace(first_open.receipt, broker_seq=first_command_seq),
        broker_inputs=(replace(first_open.broker_inputs[0], broker_seq=first_command_seq),),
        balances=(BalanceWrite("BTC", Decimal("0.007"), Decimal("0.003"), 4),),
        order=replace(
            first_open.order,
            quantity=Decimal("0.003"),
            held_amount=Decimal("0.003"),
            accepted_broker_seq=first_command_seq,
        ),
        journals=(
            replace(
                first_open.journals[0],
                entries=(
                    LedgerEntry("paper.held", "BTC", Decimal("0.003"), Decimal(0)),
                    LedgerEntry("paper.available", "BTC", Decimal(0), Decimal("0.003")),
                ),
            ),
        ),
    )
    shared_source_key = "book:shared-sell-fifo:sales"
    first_fill_id = engine_id("fill", first_open.order.order_id, shared_source_key)
    second_order_id = next(
        stable_id(f"shared-sell-second:{number}")
        for number in range(100)
        if engine_id("fill", stable_id(f"shared-sell-second:{number}"), shared_source_key)
        < first_fill_id
    )
    second_command_seq = first_command_seq + 1
    second_key = "create-shared-sell-second"
    second_hash = digest(second_key)
    second_source = f"command:{second_key}"
    second_authorization = "auth-shared-sell-second"
    second_order = replace(
        first_open.order,
        order_id=second_order_id,
        client_order_id="client-shared-sell-second",
        authorization_id=second_authorization,
        quantity=Decimal("0.005"),
        held_amount=Decimal("0.005"),
        accepted_broker_seq=second_command_seq,
    )
    second_open = replace(
        first_open,
        receipt=replace(
            first_open.receipt,
            idempotency_key=second_key,
            request_hash=second_hash,
            paper_order_id=second_order_id,
            authorization_id=second_authorization,
            broker_seq=second_command_seq,
            response={"order_id": second_order_id, "status": "OPEN"},
        ),
        authorization_attempt=replace(
            first_open.authorization_attempt,
            authorization_id=second_authorization,
            authorization_nonce="nonce-shared-sell-second",
            idempotency_key=second_key,
            request_hash=second_hash,
        ),
        broker_inputs=(
            replace(
                first_open.broker_inputs[0],
                broker_seq=second_command_seq,
                source_key=second_source,
                payload_hash=second_hash,
            ),
        ),
        balances=(BalanceWrite("BTC", Decimal("0.002"), Decimal("0.008"), 5),),
        order=second_order,
        order_events=(
            replace(
                first_open.order_events[0],
                event_id=stable_id("accepted-shared-sell-second"),
                order_id=second_order_id,
                source_key=second_source,
                payload_hash=second_hash,
            ),
        ),
        journals=(
            replace(
                first_open.journals[0],
                journal_id=stable_id("hold-shared-sell-second"),
                business_event_id=second_order_id,
                entries=(
                    LedgerEntry("paper.held", "BTC", Decimal("0.005"), Decimal(0)),
                    LedgerEntry("paper.available", "BTC", Decimal(0), Decimal("0.005")),
                ),
            ),
        ),
        outbox=(
            outbox(
                "paper.order.accepted.v1",
                second_order_id,
                1,
                {"order_id": second_order_id},
            ),
        ),
    )
    store.commit(first_open)
    store.commit(second_open)

    hydrated = store.hydrate_engine(initial.account_id)
    shared_book_seq = second_command_seq + 1
    shared_book = BrokerInputWrite(
        shared_book_seq,
        initial.account_id,
        "RECORDED_BOOK",
        shared_source_key,
        engine_id(
            "observation",
            "BTCUSDT",
            "12000.000000000000000000",
            "12001.000000000000000000",
            "0.080000000000000000",
        ),
        NOW,
        Decimal("0.08"),
        "BTCUSDT",
        Decimal("12000"),
        Decimal("12001"),
    )

    def actual_sale_write(
        order_id: str, *, btc_version: int, usdt_version: int
    ) -> AtomicPaperWrite:
        fill = hydrated.apply_book_observation(
            order_id=order_id,
            observation_id=shared_source_key,
            best_bid_text="12000",
            best_ask_text="12001",
            displayed_quantity_text="0.08",
        )
        assert fill is not None
        order = hydrated.orders[order_id]
        consumptions = tuple(
            item for item in hydrated.consumptions if item.source_fill_id == fill.fill_id
        )
        journals = tuple(
            journal
            for journal in hydrated.journals.values()
            if journal.business_event_id == fill.fill_id
        )
        return AtomicPaperWrite(
            initial.account_id,
            "test",
            None,
            None,
            (shared_book,),
            (
                BalanceWrite("BTC", hydrated.available["BTC"], hydrated.held["BTC"], btc_version),
                BalanceWrite("USDT", hydrated.available["USDT"], Decimal(0), usdt_version),
            ),
            order,
            (
                OrderEventWrite(
                    stable_id(f"filled-{fill.fill_id}"),
                    order_id,
                    order.version,
                    "paper.order.filled.v1",
                    shared_source_key,
                    digest(fill.fill_id),
                    NOW,
                ),
            ),
            (fill,),
            (),
            consumptions,
            journals,
            (
                outbox(
                    "paper.order.filled.v1",
                    order_id,
                    order.version,
                    {"order_id": order_id, "fill_id": fill.fill_id},
                ),
            ),
            NOW,
        )

    first_sale = actual_sale_write(first_open.order.order_id, btc_version=6, usdt_version=5)
    second_sale = actual_sale_write(second_order_id, btc_version=7, usdt_version=6)
    assert second_sale.fills[0].fill_id < first_sale.fills[0].fill_id
    assert sum(item.quote_basis for item in first_sale.consumptions) == Decimal("30.03")
    assert sum(item.quote_basis for item in second_sale.consumptions) == Decimal("53.053")
    assert first_sale.fills[0].broker_seq == second_sale.fills[0].broker_seq == shared_book_seq
    store.commit(first_sale)
    store.commit(second_sale)


def test_deferred_fifo_rejects_younger_first_even_if_later_sale_exhausts_older(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = complete_write(suffix="fifo-causal")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    store.commit(cancel_write(initial))

    second_open, second_fill = second_order_on_shared_observation(initial)
    second_command_seq = initial.order.accepted_broker_seq + 3
    second_open = replace(
        second_open,
        receipt=replace(second_open.receipt, broker_seq=second_command_seq),
        broker_inputs=(replace(second_open.broker_inputs[0], broker_seq=second_command_seq),),
        balances=(BalanceWrite("USDT", Decimal("99.9"), Decimal("50.05"), 3),),
        order=replace(second_open.order, accepted_broker_seq=second_command_seq),
    )
    second_book_seq = second_command_seq + 1
    second_source = replace(
        second_fill.broker_inputs[0],
        broker_seq=second_book_seq,
        source_key="book:second-buy:fifo-causal",
    )
    second_fill = replace(
        second_fill,
        created_at=datetime(2026, 7, 19, 11, 59, tzinfo=UTC),
        broker_inputs=(second_source,),
        balances=(
            BalanceWrite("USDT", Decimal("99.9"), Decimal(0), 4),
            BalanceWrite("BTC", Decimal("0.01"), Decimal(0), 3),
        ),
        fills=(
            replace(
                second_fill.fills[0],
                observation_id=second_source.source_key,
                broker_seq=second_book_seq,
            ),
        ),
        order_events=(replace(second_fill.order_events[0], source_key=second_source.source_key),),
        lots=(
            replace(
                second_fill.lots[0],
                acquired_at=datetime(2026, 7, 19, 11, 59, tzinfo=UTC),
            ),
        ),
    )
    store.commit(second_open)
    swapped_chronology = replace(
        second_fill,
        lots=(replace(second_fill.lots[0], acquired_at=NOW),),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        store.commit(swapped_chronology)
    store.commit(second_fill)

    first_open, first_sale = sell_order_after_cancel(initial)
    first_command_seq = second_book_seq + 1
    first_open = replace(
        first_open,
        receipt=replace(first_open.receipt, broker_seq=first_command_seq),
        broker_inputs=(replace(first_open.broker_inputs[0], broker_seq=first_command_seq),),
        balances=(BalanceWrite("BTC", Decimal("0.005"), Decimal("0.005"), 4),),
        order=replace(first_open.order, accepted_broker_seq=first_command_seq),
    )
    first_book_seq = first_command_seq + 1
    first_sale = replace(
        first_sale,
        broker_inputs=(replace(first_sale.broker_inputs[0], broker_seq=first_book_seq),),
        order=replace(first_sale.order, accepted_broker_seq=first_command_seq),
        balances=(
            BalanceWrite("BTC", Decimal(0), Decimal("0.005"), 6),
            BalanceWrite("USDT", Decimal("159.84"), Decimal(0), 5),
        ),
        fills=(replace(first_sale.fills[0], broker_seq=first_book_seq),),
        consumptions=(replace(first_sale.consumptions[0], lot_id=second_fill.lots[0].lot_id),),
    )

    def clone_sale(
        opened: AtomicPaperWrite,
        filled: AtomicPaperWrite,
        *,
        tag: str,
        command_seq: int,
        lot_id: str,
    ) -> tuple[AtomicPaperWrite, AtomicPaperWrite]:
        order_id = stable_id(f"sell-order:{tag}")
        fill_id = stable_id(f"sell-fill:{tag}")
        auth_id = f"auth-sell-{tag}"
        key = f"create-sell-{tag}"
        request_hash = digest(key)
        command_source = f"command:{key}"
        book_source = f"book:sell:{tag}"
        new_receipt = replace(
            opened.receipt,
            idempotency_key=key,
            request_hash=request_hash,
            paper_order_id=order_id,
            authorization_id=auth_id,
            broker_seq=command_seq,
            response={"order_id": order_id, "status": "OPEN"},
        )
        new_attempt = replace(
            opened.authorization_attempt,
            authorization_id=auth_id,
            authorization_nonce=f"nonce-{key}",
            idempotency_key=key,
            request_hash=request_hash,
        )
        new_order = replace(
            opened.order,
            order_id=order_id,
            client_order_id=f"client-{key}",
            authorization_id=auth_id,
            accepted_broker_seq=command_seq,
        )
        new_open = replace(
            opened,
            receipt=new_receipt,
            authorization_attempt=new_attempt,
            broker_inputs=(
                replace(
                    opened.broker_inputs[0],
                    broker_seq=command_seq,
                    source_key=command_source,
                    payload_hash=request_hash,
                ),
            ),
            balances=(BalanceWrite("BTC", Decimal(0), Decimal("0.01"), 5),),
            order=new_order,
            order_events=(
                replace(
                    opened.order_events[0],
                    event_id=stable_id(f"accepted-{tag}"),
                    order_id=order_id,
                    source_key=command_source,
                    payload_hash=request_hash,
                ),
            ),
            journals=(
                replace(
                    opened.journals[0],
                    journal_id=stable_id(f"hold-{tag}"),
                    business_event_id=order_id,
                ),
            ),
            outbox=(outbox("paper.order.accepted.v1", order_id, 1, {"order_id": order_id}),),
        )
        book_seq = command_seq + 1
        new_fill = replace(
            filled.fills[0],
            fill_id=fill_id,
            order_id=order_id,
            observation_id=book_source,
            broker_seq=book_seq,
        )
        new_filled = replace(
            filled,
            broker_inputs=(
                replace(filled.broker_inputs[0], broker_seq=book_seq, source_key=book_source),
            ),
            balances=(
                BalanceWrite("BTC", Decimal(0), Decimal(0), 7),
                BalanceWrite("USDT", Decimal("219.78"), Decimal(0), 6),
            ),
            order=replace(
                filled.order,
                order_id=order_id,
                client_order_id=new_order.client_order_id,
                authorization_id=auth_id,
                accepted_broker_seq=command_seq,
            ),
            order_events=(
                replace(
                    filled.order_events[0],
                    event_id=stable_id(f"filled-{tag}"),
                    order_id=order_id,
                    source_key=book_source,
                    payload_hash=digest(fill_id),
                ),
            ),
            fills=(new_fill,),
            consumptions=(
                replace(
                    filled.consumptions[0],
                    consumption_id=stable_id(f"consume-{tag}"),
                    lot_id=lot_id,
                    source_fill_id=fill_id,
                ),
            ),
            journals=tuple(
                replace(
                    journal,
                    journal_id=stable_id(f"{journal.journal_kind}-{tag}"),
                    business_event_id=fill_id,
                )
                for journal in filled.journals
            ),
            outbox=(
                outbox(
                    "paper.order.filled.v1",
                    order_id,
                    2,
                    {"order_id": order_id, "fill_id": fill_id},
                ),
            ),
        )
        return new_open, new_filled

    second_sale_open, second_sale = clone_sale(
        first_open,
        first_sale,
        tag="fifo-causal-second",
        command_seq=first_book_seq + 1,
        lot_id=initial.lots[0].lot_id,
    )
    store.commit(first_open)
    store.commit(second_sale_open)

    real_connection = psycopg.connect(DATABASE_URL)

    class SharedConnection:
        def __enter__(self) -> psycopg.Connection[tuple[object, ...]]:
            return real_connection

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, *args: object, **kwargs: object) -> object:
            return real_connection.execute(*args, **kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "paper_engine.persistence.psycopg.connect", lambda *_args, **_kwargs: SharedConnection()
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        with real_connection:
            PostgresPaperStore(PAPER_WRITER_URL).commit(first_sale)
            PostgresPaperStore(PAPER_WRITER_URL).commit(second_sale)


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
    authorization_id = write.authorization_attempt.authorization_id
    baseline_response = {"error_code": "INSUFFICIENT_FUNDS"}

    def set_receipt_response(response: dict[str, str]) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("SET session_replication_role='replica'")
            connection.execute(
                "UPDATE paper_command_receipts SET response=%s WHERE authorization_id=%s",
                (Jsonb(response), authorization_id),
            )
            connection.execute("SET session_replication_role='origin'")

    for corrupt_response in (
        {"reason_code": "INSUFFICIENT_FUNDS"},
        {
            "error_code": "INSUFFICIENT_FUNDS",
            "reason_code": "INSUFFICIENT_FUNDS",
        },
        {},
    ):
        set_receipt_response(corrupt_response)
        try:
            with pytest.raises(RuntimeError, match="PAPER_REJECTED_RECEIPT_CORRUPT"):
                PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(write.account_id)
        finally:
            set_receipt_response(baseline_response)

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "UPDATE paper_authorization_attempts SET request_hash=%s WHERE authorization_id=%s",
            (digest("corrupt-legacy-rejected-receipt"), authorization_id),
        )
        connection.execute("SET session_replication_role='origin'")
    try:
        with pytest.raises(RuntimeError, match="PAPER_REJECTED_RECEIPT_CORRUPT"):
            PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(write.account_id)
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("SET session_replication_role='replica'")
            connection.execute(
                "UPDATE paper_authorization_attempts SET request_hash=%s WHERE authorization_id=%s",
                (write.receipt.request_hash, authorization_id),
            )
            connection.execute("SET session_replication_role='origin'")

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "ALTER TABLE paper_authorization_attempts DROP CONSTRAINT ck_paper_attempt_reason"
        )
        connection.execute(
            "UPDATE paper_authorization_attempts SET outcome='CONSUMED_ORDER_CREATED' "
            "WHERE authorization_id=%s",
            (authorization_id,),
        )
        connection.execute("SET session_replication_role='origin'")
    try:
        with pytest.raises(RuntimeError, match="PAPER_REJECTED_RECEIPT_CORRUPT"):
            PostgresPaperStore(PAPER_WRITER_URL).hydrate_engine(write.account_id)
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("SET session_replication_role='replica'")
            connection.execute(
                "UPDATE paper_authorization_attempts SET outcome='BLOCKED' "
                "WHERE authorization_id=%s",
                (authorization_id,),
            )
            connection.execute(
                "ALTER TABLE paper_authorization_attempts "
                "ADD CONSTRAINT ck_paper_attempt_reason "
                "CHECK ((outcome='BLOCKED')=(reason_code IS NOT NULL))"
            )
            connection.execute("SET session_replication_role='origin'")
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
            "0.050000000000000000",
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
            "0.050000000000000000",
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
    holdless, _ = split_open_and_fill(suffix="missing-hold-authority")
    holdless = replace(
        holdless,
        balances=(BalanceWrite("USDT", Decimal("200"), Decimal(0), 1),),
        journals=(holdless.journals[0],),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(holdless)
    reversed_causality = complete_write(suffix="reversed-causality")
    evidence_seq = reversed_causality.order.accepted_broker_seq + 2
    reversed_causality = replace(
        reversed_causality,
        broker_inputs=(
            reversed_causality.broker_inputs[0],
            replace(reversed_causality.broker_inputs[1], broker_seq=evidence_seq),
        ),
        fills=(
            replace(
                reversed_causality.fills[0],
                broker_seq=reversed_causality.order.accepted_broker_seq + 1,
            ),
        ),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(reversed_causality)
    forward_causality = complete_write(suffix="forward-causality")
    forward_causality = replace(
        forward_causality,
        fills=(
            replace(
                forward_causality.fills[0],
                broker_seq=forward_causality.broker_inputs[1].broker_seq + 1,
            ),
        ),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(forward_causality)
    missing_fill_event = complete_write(suffix="missing-fill-event")
    missing_fill_event = replace(
        missing_fill_event,
        order_events=(missing_fill_event.order_events[0],),
    )
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="fill/order consistency|Unlinked Paper outbox",
    ):
        PostgresPaperStore(DATABASE_URL).commit(missing_fill_event)
    wrong_fill_event_version = complete_write(suffix="wrong-fill-event-version")
    wrong_fill_event_version = replace(
        wrong_fill_event_version,
        order_events=(
            wrong_fill_event_version.order_events[0],
            replace(wrong_fill_event_version.order_events[1], order_version=3),
        ),
    )
    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.RaiseException)):
        PostgresPaperStore(DATABASE_URL).commit(wrong_fill_event_version)
    two_fills_one_event = complete_write(suffix="two-fills-one-event")
    second_fill = replace(two_fills_one_event.fills[0], fill_id=stable_id("second-fill"))
    two_fills_one_event = replace(
        two_fills_one_event,
        fills=(two_fills_one_event.fills[0], second_fill),
    )
    with pytest.raises((psycopg.errors.UniqueViolation, psycopg.errors.RaiseException)):
        PostgresPaperStore(DATABASE_URL).commit(two_fills_one_event)
    mismatched_lot = complete_write(suffix="mismatched-lot")
    lot = replace(
        mismatched_lot.lots[0],
        acquired_quantity=Decimal("0.001"),
        quote_cost=Decimal("10.01"),
    )
    mismatched_lot = replace(mismatched_lot, lots=(lot,))
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        PostgresPaperStore(DATABASE_URL).commit(mismatched_lot)


def test_database_rejects_noncanonical_ledger_transaction_id() -> None:
    write = complete_write(suffix="unicode-ledger-id")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(write)
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_paper_ledger_transaction_id"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO paper_ledger_transactions"
                "(transaction_id,account_id,business_event_type,business_event_id,"
                "journal_kind,posted_at) VALUES (%s,%s,%s,%s,'PHYSICAL',%s)",
                ("원장-1", write.account_id, "paper.test", "unicode-ledger", NOW),
            )
    invalid_outbox = outbox(
        "ledger.transaction.posted.v1",
        "원장-1",
        1,
        {"transaction_id": "원장-1"},
    )
    invalid_outbox_write = rejected_write(suffix="invalid-ledger-outbox")
    with pytest.raises(ValueError, match="INVALID_PAPER_OUTBOX_CONTRACT"):
        store.commit(
            replace(
                invalid_outbox_write,
                outbox=(*invalid_outbox_write.outbox, invalid_outbox),
            )
        )


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


def test_terminal_order_cannot_append_a_new_accepted_version() -> None:
    initial = complete_write(suffix="terminal-monotonic")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    cancelled = cancel_write(initial)
    store.commit(cancelled)
    assert cancelled.order is not None and cancelled.receipt is not None
    broker_seq = cancelled.receipt.broker_seq + 1
    source_key = f"command:forged-reopen:{initial.account_id}"
    event = outbox(
        "paper.order.accepted.v1",
        cancelled.order.order_id,
        4,
        {"order_id": cancelled.order.order_id},
    )
    with pytest.raises(psycopg.errors.RaiseException, match="fill/order consistency"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO paper_broker_inputs"
                "(broker_seq,account_id,source_kind,source_key,payload_hash,observed_at) "
                "VALUES (%s,%s,'TEST_COMMAND',%s,%s,%s)",
                (broker_seq, initial.account_id, source_key, digest(source_key), NOW),
            )
            connection.execute(
                "UPDATE paper_orders SET version=4 WHERE order_id=%s",
                (cancelled.order.order_id,),
            )
            connection.execute(
                "INSERT INTO paper_order_events"
                "(event_id,order_id,order_version,event_type,source_key,payload_hash,occurred_at) "
                "VALUES (%s,%s,4,'paper.order.accepted.v1',%s,%s,%s)",
                (
                    stable_id(f"forged-reopen:{cancelled.order.order_id}"),
                    cancelled.order.order_id,
                    source_key,
                    digest("forged-reopen"),
                    NOW,
                ),
            )
            connection.execute(
                "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (
                    event.event_id,
                    event.event_type,
                    json.dumps(event.payload),
                    event.payload_hash,
                    event.occurred_at,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.aggregate_version,
                ),
            )
            connection.execute(
                "INSERT INTO paper_outbox_links(event_id,account_id) VALUES (%s,%s)",
                (event.event_id, initial.account_id),
            )


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

    empty_journal_id = stable_id("empty-journal")
    with pytest.raises(psycopg.errors.RaiseException, match="at least one entry"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO paper_ledger_transactions
                    (transaction_id,account_id,business_event_type,business_event_id,
                     journal_kind,posted_at)
                VALUES (%s,%s,'paper.test','empty','PHYSICAL',%s)
                """,
                (empty_journal_id, write.account_id, NOW),
            )

    unpaired_reversal_id = stable_id("unpaired-reversal")
    with pytest.raises(psycopg.errors.RaiseException, match="correction pair"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO paper_ledger_transactions
                    (transaction_id,account_id,business_event_type,business_event_id,
                     journal_kind,reversal_of,posted_at)
                VALUES (%s,%s,'paper.correction','correction-x',
                        'PHYSICAL',%s,%s)
                """,
                (
                    unpaired_reversal_id,
                    write.account_id,
                    write.journals[0].journal_id,
                    NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO paper_ledger_entries
                    (transaction_id,line_no,account_code,commodity,debit,credit)
                SELECT %s,line_no,account_code,commodity,credit,debit
                FROM paper_ledger_entries WHERE transaction_id=%s
                """,
                (unpaired_reversal_id, write.journals[0].journal_id),
            )

    wrong_kind_reversal_id = stable_id("wrong-kind-reversal")
    wrong_kind_replacement_id = stable_id("wrong-kind-replacement")
    with pytest.raises(psycopg.errors.RaiseException, match="target or kind"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO paper_ledger_transactions"
                "(transaction_id,account_id,business_event_type,business_event_id,"
                "journal_kind,reversal_of,posted_at) VALUES "
                "(%s,%s,'paper.correction','wrong-kind',"
                "'VALUATION',%s,%s)",
                (
                    wrong_kind_reversal_id,
                    write.account_id,
                    write.journals[0].journal_id,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO paper_ledger_transactions"
                "(transaction_id,account_id,business_event_type,business_event_id,"
                "journal_kind,replacement_for,posted_at) VALUES "
                "(%s,%s,'paper.replacement','wrong-kind',"
                "'VALUATION',%s,%s)",
                (
                    wrong_kind_replacement_id,
                    write.account_id,
                    write.journals[0].journal_id,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO paper_ledger_entries"
                "(transaction_id,line_no,account_code,commodity,debit,credit) "
                "SELECT %s,line_no,account_code,commodity,credit,debit "
                "FROM paper_ledger_entries WHERE transaction_id=%s",
                (wrong_kind_reversal_id, write.journals[0].journal_id),
            )
            connection.execute(
                "INSERT INTO paper_ledger_entries"
                "(transaction_id,line_no,account_code,commodity,debit,credit) "
                "SELECT %s,line_no,account_code,commodity,debit,credit "
                "FROM paper_ledger_entries WHERE transaction_id=%s",
                (wrong_kind_replacement_id, write.journals[0].journal_id),
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
        ("paper_authorization_worker_state", "instance_id"),
        ("paper_authorization_worker_state", "status"),
        ("paper_authorization_worker_state", "started_at"),
        ("paper_authorization_worker_state", "heartbeat_at"),
        ("paper_authorization_worker_state", "last_progress_at"),
        ("paper_authorization_worker_state", "last_result"),
        ("paper_authorization_worker_state", "last_error_code"),
        ("paper_authorization_worker_state", "stopped_at"),
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
    assert write.authorization_attempt is not None
    authorization_id = write.authorization_attempt.authorization_id
    authorization = outbox(
        "paper.authorization.attempted.v1",
        engine_id("authorization", authorization_id),
        1,
        {"authorization_id": authorization_id, "outcome": "CONSUMED"},
    )

    def append_direct(item: OutboxWrite) -> None:
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (
                    item.event_id,
                    item.event_type,
                    json.dumps(item.payload),
                    item.payload_hash,
                    item.occurred_at,
                    item.aggregate_type,
                    item.aggregate_id,
                    item.aggregate_version,
                ),
            )

    forged_event_id = replace(
        authorization,
        event_id="f" * 64,
        payload={**authorization.payload, "event_id": "f" * 64},
    )
    with pytest.raises(psycopg.errors.RaiseException, match="derived Paper outbox"):
        append_direct(forged_event_id)
    forged_payload_hash = replace(
        authorization,
        payload_hash="e" * 64,
        payload={**authorization.payload, "payload_hash": "e" * 64},
    )
    with pytest.raises(psycopg.errors.RaiseException, match="derived Paper outbox"):
        append_direct(forged_payload_hash)
    forged_aggregate = outbox(
        "paper.authorization.attempted.v1",
        "c" * 64,
        1,
        {"authorization_id": authorization_id, "outcome": "CONSUMED"},
    )
    with pytest.raises(psycopg.errors.RaiseException, match="derived Paper outbox"):
        append_direct(forged_aggregate)


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


def test_cancel_outbox_binds_exact_receipt_and_rejects_non_ascii_identity() -> None:
    initial = complete_write(suffix="cancel-binding")
    store = PostgresPaperStore(PAPER_WRITER_URL)
    store.commit(initial)
    cancelled = cancel_write(initial)
    store.commit(cancelled)
    assert cancelled.order is not None
    for wrong_cancel_id in ("cancel-other", "취소-1"):
        forged = outbox(
            "paper.order.cancelled.v1",
            cancelled.order.order_id,
            cancelled.order.version,
            {"order_id": cancelled.order.order_id, "cancel_id": wrong_cancel_id},
        )
        with pytest.raises(psycopg.errors.RaiseException):
            with psycopg.connect(PAPER_WRITER_URL) as connection:
                connection.execute(
                    "SELECT append_paper_outbox(%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                    (
                        forged.event_id,
                        forged.event_type,
                        json.dumps(forged.payload),
                        forged.payload_hash,
                        forged.occurred_at,
                        forged.aggregate_type,
                        forged.aggregate_id,
                        forged.aggregate_version,
                    ),
                )

    assert cancelled.receipt is not None
    duplicate_key = "cancel-second-receipt"
    duplicate_hash = digest(duplicate_key)
    duplicate_authorization = "auth-cancel-second-receipt"
    duplicate_seq = cancelled.receipt.broker_seq + 1
    with pytest.raises(psycopg.errors.RaiseException, match="receipt/order/attempt/input"):
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "INSERT INTO paper_broker_inputs"
                "(broker_seq,account_id,source_kind,source_key,payload_hash,observed_at) "
                "VALUES (%s,%s,'TEST_COMMAND',%s,%s,%s)",
                (
                    duplicate_seq,
                    initial.account_id,
                    f"command:{duplicate_key}",
                    duplicate_hash,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO paper_authorization_attempts"
                "(authorization_id,authorization_nonce,account_id,command_scope,"
                "idempotency_key,namespace,request_hash,outcome,created_at) "
                "VALUES (%s,%s,%s,'paper.cancel.v1',%s,'test',%s,"
                "'CONSUMED_ORDER_CANCELLED',%s)",
                (
                    duplicate_authorization,
                    "nonce-cancel-second-receipt",
                    initial.account_id,
                    duplicate_key,
                    duplicate_hash,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO paper_command_receipts"
                "(scope,idempotency_key,account_id,request_hash,outcome,paper_order_id,"
                "authorization_id,broker_seq,response,created_at) "
                "VALUES ('paper.cancel.v1',%s,%s,%s,'ORDER_CANCELLED',%s,%s,%s,%s::jsonb,%s)",
                (
                    duplicate_key,
                    initial.account_id,
                    duplicate_hash,
                    cancelled.order.order_id,
                    duplicate_authorization,
                    duplicate_seq,
                    json.dumps({"order_id": cancelled.order.order_id, "status": "CANCELLED"}),
                    NOW,
                ),
            )


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
