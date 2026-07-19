from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

import psycopg
import pytest

from risk_engine import PostgresRiskStore, RiskPersistenceStage, evaluate_risk
from test_risk_engine import risk_input


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
NOW = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)


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
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            run("docker", "compose", "stop", "postgres")


def test_risk_decision_persistence_is_atomic_unique_and_replay_stable() -> None:
    payload = risk_input()
    decision = evaluate_risk(payload)
    store = PostgresRiskStore(DATABASE_URL)

    first = store.persist_decision(payload, decision, recorded_at=NOW)
    replay = store.persist_decision(deepcopy(payload), decision, recorded_at=NOW)
    assert first.created is True
    assert replay.created is False
    assert replay.decision_id == first.decision_id
    assert replay.risk_input_digest == first.risk_input_digest
    assert replay.decision_hash == first.decision_hash
    assert replay.outbox_event_id == first.outbox_event_id

    failed_input = risk_input()
    failed_input["data"]["evidence_hash"] = "b" * 64  # type: ignore[index]
    failed_decision = evaluate_risk(failed_input)
    with pytest.raises(RuntimeError, match="INJECTED_RISK_FAILURE:decision"):
        store.persist_decision(
            failed_input,
            failed_decision,
            recorded_at=NOW,
            _fail_after=RiskPersistenceStage.DECISION,
        )

    with psycopg.connect(DATABASE_URL) as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM risk_decisions),"
            "(SELECT count(*) FROM risk_outbox_links WHERE aggregate_kind='risk-decision'),"
            "(SELECT count(*) FROM risk_decisions WHERE risk_input_digest=%s)",
            (failed_decision.risk_input_digest,),
        ).fetchone()
        assert counts == (1, 1, 0)
        with pytest.raises(psycopg.Error):
            connection.execute(
                "UPDATE risk_decisions SET verdict='DENIED' WHERE decision_id=%s",
                (first.decision_id,),
            )
