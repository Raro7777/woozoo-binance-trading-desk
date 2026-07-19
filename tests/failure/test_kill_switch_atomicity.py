from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

import psycopg
import pytest

from paper_engine.models import Journal, LedgerEntry, OrderSide, OrderStatus, PaperOrder
from paper_engine.persistence import (
    AtomicPaperWrite,
    AuthorizationAttemptWrite,
    BalanceWrite,
    BrokerInputWrite,
    CommandReceiptWrite,
    KillCancelStage,
    OrderEventWrite,
    PostgresPaperStore,
)
from risk_engine import KillActivation, KillPersistenceStage, PostgresKillSwitch
from test_paper_postgres_persistence import digest, outbox, stable_id


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
PAPER_WRITER_URL = "postgresql://woozoo_paper_engine@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
NOW = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
ACCOUNT_ID = "account-kill-001-batch"
OPEN_ORDER_COUNT = 101
INITIAL_QUOTE = Decimal("20000.000000000000000000")
HELD_PER_ORDER = Decimal("100.100000000000000000")


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
                    raise TimeoutError("could not acquire infrastructure lock")
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
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            yield
        finally:
            # KILL-001 intentionally leaves monotonic ACTIVE state and immutable
            # cancellation history. The compose database is a disposable test
            # authority, so remove its exact volume instead of pretending those
            # effects can be reversed by a later migration test.
            run("docker", "compose", "down", "-v")


def open_order_write(index: int) -> AtomicPaperWrite:
    suffix = f"kill-001-{index:03d}"
    order_id = stable_id(f"order-{suffix}")
    request_hash = digest(f"request-{suffix}")
    idempotency_key = f"create-{suffix}"
    authorization_id = f"auth-{suffix}"
    source_key = f"command:{suffix}"
    broker_seq = 10_000 + index
    order = PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{suffix}",
        authorization_id=authorization_id,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.010000000000000000"),
        limit_price=Decimal("10000.000000000000000000"),
        accepted_broker_seq=broker_seq,
        status=OrderStatus.OPEN,
        filled_quantity=Decimal(0),
        held_asset="USDT",
        held_amount=HELD_PER_ORDER,
        version=1,
    )
    receipt = CommandReceiptWrite(
        "paper.create.v1",
        idempotency_key,
        ACCOUNT_ID,
        request_hash,
        "ORDER_CREATED",
        order_id,
        authorization_id,
        broker_seq,
        {"order_id": order_id, "status": "OPEN"},
        NOW,
    )
    attempt = AuthorizationAttemptWrite(
        authorization_id,
        f"nonce-{suffix}",
        ACCOUNT_ID,
        receipt.scope,
        idempotency_key,
        "test",
        request_hash,
        "CONSUMED_ORDER_CREATED",
        None,
        NOW,
    )
    held_total = HELD_PER_ORDER * index
    journals = [
        Journal(
            stable_id(f"hold-journal-{suffix}"),
            "paper.hold",
            order_id,
            "PHYSICAL",
            (
                LedgerEntry("paper.held", "USDT", HELD_PER_ORDER, Decimal(0)),
                LedgerEntry("paper.available", "USDT", Decimal(0), HELD_PER_ORDER),
            ),
        )
    ]
    if index == 1:
        journals.insert(
            0,
            Journal(
                stable_id("seed-journal-kill-001"),
                "paper.seed",
                "seed-kill-001",
                "PHYSICAL",
                (
                    LedgerEntry("paper.available", "USDT", INITIAL_QUOTE, Decimal(0)),
                    LedgerEntry("paper.opening-equity", "USDT", Decimal(0), INITIAL_QUOTE),
                ),
            ),
        )
    accepted = outbox("paper.order.accepted.v1", order_id, 1, {"order_id": order_id})
    return AtomicPaperWrite(
        account_id=ACCOUNT_ID,
        namespace="test",
        receipt=receipt,
        authorization_attempt=attempt,
        broker_inputs=(
            BrokerInputWrite(broker_seq, ACCOUNT_ID, "TEST_COMMAND", source_key, request_hash, NOW),
        ),
        balances=(BalanceWrite("USDT", INITIAL_QUOTE - held_total, held_total, index),),
        order=order,
        order_events=(
            OrderEventWrite(
                accepted.event_id,
                order_id,
                1,
                "paper.order.accepted.v1",
                source_key,
                request_hash,
                NOW,
            ),
        ),
        fills=(),
        lots=(),
        consumptions=(),
        journals=tuple(journals),
        outbox=(accepted,),
        created_at=NOW,
    )


def test_kill_001_activation_serializes_and_cancel_batches_replay_once() -> None:
    paper = PostgresPaperStore(DATABASE_URL)
    writes = [open_order_write(index) for index in range(1, OPEN_ORDER_COUNT + 1)]
    for write in writes:
        paper.commit(write)
    assert writes[0].order is not None

    command = KillActivation(
        request_id="kill-001",
        expected_version=0,
        trigger_kind="MANUAL",
        actor_id="operator:safety-lead",
        reason_code="MANUAL_SAFETY_STOP",
        reason="failure-injection safety stop",
        observed_at=NOW,
        context_digest="a" * 64,
    )
    kill = PostgresKillSwitch(DATABASE_URL)
    with pytest.raises(RuntimeError, match="INJECTED_KILL_FAILURE:outbox"):
        kill.activate(command, _fail_after=KillPersistenceStage.OUTBOX)
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (False, 0)
        assert connection.execute("SELECT count(*) FROM kill_switch_events").fetchone() == (0,)

    # The security-definer function gives Paper only a shared row lock. Activation
    # must wait for that local Paper transaction to finish.
    paper_lock = psycopg.connect(PAPER_WRITER_URL)
    try:
        assert paper_lock.execute(
            "SELECT active,version FROM paper_lock_kill_barrier()"
        ).fetchone() == (
            False,
            0,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            activation_future = executor.submit(kill.activate, command)
            with pytest.raises(FutureTimeout):
                activation_future.result(timeout=0.2)
            paper_lock.commit()
            activation = activation_future.result(timeout=5)
    finally:
        paper_lock.close()

    replay_activation = kill.activate(command)
    assert activation.created is True
    assert replay_activation.created is False
    assert replay_activation.activation_event_id == activation.activation_event_id

    blocked_write = replace(open_order_write(OPEN_ORDER_COUNT + 1), kill_switch_version=1)
    with pytest.raises(RuntimeError, match="PAPER_KILL_SWITCH_ACTIVE"):
        paper.commit(blocked_write)

    with psycopg.connect(DATABASE_URL) as connection:
        payload_hash = connection.execute(
            "SELECT payload_hash FROM outbox_events WHERE event_id=%s",
            (activation.outbox_event_id,),
        ).fetchone()[0]

    with pytest.raises(RuntimeError, match="INJECTED_KILL_CANCEL_FAILURE:outbox"):
        paper.consume_kill_activation(
            activation.activation_event_id,
            payload_hash,
            received_at=NOW,
            _fail_after=KillCancelStage.OUTBOX,
        )
    with psycopg.connect(DATABASE_URL) as connection:
        rolled_back = connection.execute(
            "SELECT (SELECT count(*) FROM paper_kill_inbox),"
            "(SELECT count(*) FROM paper_kill_cancel_batches),"
            "(SELECT status FROM paper_orders WHERE order_id=%s)",
            (writes[0].order.order_id,),
        ).fetchone()
        assert rolled_back == (0, 0, "OPEN")

    cancelled = paper.consume_kill_activation(
        activation.activation_event_id, payload_hash, received_at=NOW
    )
    second_batch = paper.consume_kill_activation(
        activation.activation_event_id, payload_hash, received_at=NOW
    )
    duplicate = paper.consume_kill_activation(
        activation.activation_event_id, payload_hash, received_at=NOW
    )
    assert cancelled.batch_created is True
    assert cancelled.cancelled_count == 100
    assert cancelled.has_more is True
    assert second_batch.batch_created is True
    assert second_batch.cancelled_count == 1
    assert second_batch.has_more is False
    assert duplicate.batch_created is False
    assert duplicate.cancelled_count == 0

    with psycopg.connect(DATABASE_URL) as connection:
        counts = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM paper_kill_inbox),"
            "(SELECT count(*) FROM paper_kill_cancel_batches),"
            "(SELECT count(*) FROM paper_kill_cancel_items),"
            "(SELECT count(*) FROM paper_order_events "
            " WHERE event_type='paper.order.cancelled.v1'),"
            "(SELECT count(*) FROM paper_ledger_transactions "
            " WHERE business_event_type='paper.hold-release'),"
            "(SELECT count(*) FROM paper_orders WHERE status='CANCELLED')",
        ).fetchone()
        assert counts == (1, 2, 101, 101, 101, 101)
        assert connection.execute(
            "SELECT array_agg(cancelled_count ORDER BY cancelled_count DESC) "
            "FROM paper_kill_cancel_batches"
        ).fetchone() == ([100, 1],)
