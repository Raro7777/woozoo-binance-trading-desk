from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from threading import Event
from typing import Iterator

import psycopg
import pytest

from paper_engine.persistence import KillCancelStage, PostgresPaperStore
from risk_engine import KillActivation, PostgresKillSwitch
from test_kill_switch_atomicity import OPEN_ORDER_COUNT, open_order_write
from test_paper_postgres_persistence import split_open_and_fill


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
NOW = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)
FAULTS = (
    "paper_create_lock_before_activation",
    "activation_lock_before_paper_create",
    "paper_fill_lock_before_activation",
    "activation_lock_before_paper_fill",
    "one_hundred_and_one_open_orders",
    "crash_before_batch_commit",
    "crash_after_batch_commit_before_ack",
    "duplicate_activation_delivery",
)


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


@contextmanager
def isolated_postgres() -> Iterator[None]:
    lock_path = Path(tempfile.gettempdir()) / "woozoo-docker-integration.lock"
    with lock_path.open("a+b") as lock:
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
            run("docker", "compose", "up", "-d", "--wait", "postgres")
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            yield
        finally:
            run("docker", "compose", "down", "-v")
            if os.name == "nt":
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                except PermissionError:
                    pass
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def activation(fault: str) -> KillActivation:
    return KillActivation(
        request_id=f"kill-001-{fault}",
        expected_version=0,
        trigger_kind="MANUAL",
        actor_id="operator:safety-lead",
        reason_code="MANUAL_SAFETY_STOP",
        reason=f"failure matrix {fault}",
        observed_at=NOW,
        context_digest="e" * 64,
    )


def payload_hash(event_id: str) -> str:
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT payload_hash FROM outbox_events WHERE event_id=%s", (event_id,)
        ).fetchone()
    assert row is not None
    return row[0]


def assert_active() -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (True, 1)


@pytest.mark.parametrize("fault", FAULTS, ids=FAULTS)
def test_kill_001_fault_matrix(fault: str) -> None:
    with isolated_postgres():
        paper = PostgresPaperStore(DATABASE_URL)
        kill = PostgresKillSwitch(DATABASE_URL)

        if fault in {"paper_create_lock_before_activation", "paper_fill_lock_before_activation"}:
            if fault == "paper_create_lock_before_activation":
                write = open_order_write(1)
            else:
                opened, write = split_open_and_fill(suffix="kill-001-fill-before")
                assert paper.commit(opened).created is True
            acquired = Event()
            release = Event()

            def pause_with_shared_barrier() -> None:
                acquired.set()
                assert release.wait(timeout=5)

            with ThreadPoolExecutor(max_workers=2) as executor:
                paper_future = executor.submit(
                    paper.commit, write, _after_barrier_acquired=pause_with_shared_barrier
                )
                assert acquired.wait(timeout=5)
                activation_future = executor.submit(kill.activate, activation(fault))
                with pytest.raises(FutureTimeout):
                    activation_future.result(timeout=0.2)
                release.set()
                assert paper_future.result(timeout=10).created is True
                assert activation_future.result(timeout=10).created is True
            assert_active()
            return

        if fault in {"activation_lock_before_paper_create", "activation_lock_before_paper_fill"}:
            if fault == "activation_lock_before_paper_fill":
                opened, write = split_open_and_fill(suffix="kill-001-fill-after")
                assert paper.commit(opened).created is True
            else:
                write = open_order_write(1)
            assert kill.activate(activation(fault)).created is True
            with pytest.raises(RuntimeError, match="PAPER_KILL_SWITCH_ACTIVE"):
                paper.commit(write)
            assert_active()
            return

        order_count = OPEN_ORDER_COUNT if fault == "one_hundred_and_one_open_orders" else 1
        for index in range(1, order_count + 1):
            assert paper.commit(open_order_write(index)).created is True
        activated = kill.activate(activation(fault))
        event_hash = payload_hash(activated.outbox_event_id)

        if fault == "one_hundred_and_one_open_orders":
            first = paper.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            second = paper.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            assert (first.cancelled_count, first.has_more) == (100, True)
            assert (second.cancelled_count, second.has_more) == (1, False)
        elif fault == "crash_before_batch_commit":
            with pytest.raises(RuntimeError, match="INJECTED_KILL_CANCEL_FAILURE:outbox"):
                paper.consume_kill_activation(
                    activated.activation_event_id,
                    event_hash,
                    received_at=NOW,
                    _fail_after=KillCancelStage.OUTBOX,
                )
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT (SELECT count(*) FROM paper_kill_inbox),"
                    "(SELECT count(*) FROM paper_kill_cancel_batches),"
                    "(SELECT count(*) FROM paper_kill_cancel_items)"
                ).fetchone() == (0, 0, 0)
            restarted_consumer = PostgresPaperStore(DATABASE_URL)
            resumed = restarted_consumer.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            replay = restarted_consumer.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            assert resumed.batch_created is True and resumed.cancelled_count == 1
            assert replay.batch_created is False and replay.cancelled_count == 0
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM paper_kill_inbox),"
                    "(SELECT count(*) FROM paper_kill_cancel_batches),"
                    "(SELECT count(*) FROM paper_kill_cancel_items),"
                    "(SELECT count(*) FROM paper_order_events "
                    " WHERE event_type='paper.order.cancelled.v1'),"
                    "(SELECT count(*) FROM paper_ledger_transactions "
                    " WHERE business_event_type='paper.hold-release'),"
                    "(SELECT count(*) FROM outbox_events "
                    " WHERE event_type='paper.order.cancelled.v1'),"
                    "(SELECT count(*) FROM paper_orders WHERE status='CANCELLED')"
                ).fetchone() == (1, 1, 1, 1, 1, 1, 1)
        elif fault == "crash_after_batch_commit_before_ack":
            paper.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            restarted_consumer = PostgresPaperStore(DATABASE_URL)
            replay = restarted_consumer.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            assert replay.batch_created is False and replay.cancelled_count == 0
        elif fault == "duplicate_activation_delivery":
            first = paper.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            duplicate = paper.consume_kill_activation(
                activated.activation_event_id, event_hash, received_at=NOW
            )
            assert first.cancelled_count == 1
            assert duplicate.batch_created is False and duplicate.cancelled_count == 0
        else:
            raise AssertionError(fault)
        assert_active()
