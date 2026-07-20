from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator

import psycopg
import pytest

from docker_infrastructure_lock import docker_infrastructure_lock
from paper_engine.persistence import PersistenceStage, PostgresPaperStore

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}

_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "paper_postgres_fixture", ROOT / "tests/integration/test_paper_postgres_persistence.py"
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
_FIXTURE_MODULE = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(_FIXTURE_MODULE)
complete_write = _FIXTURE_MODULE.complete_write


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
            run("docker", "compose", "stop", "postgres")


@pytest.mark.parametrize(
    "stage",
    [
        PersistenceStage.RECEIPT,
        PersistenceStage.DOMAIN,
        PersistenceStage.LOT,
        PersistenceStage.LEDGER_HEADER,
        PersistenceStage.LEDGER_ENTRY,
        PersistenceStage.OUTBOX,
    ],
)
def test_injected_failure_rolls_back_every_authoritative_row(stage: PersistenceStage) -> None:
    write = complete_write(suffix=f"failure-{stage.value}")
    with pytest.raises(RuntimeError, match=f"INJECTED_PAPER_FAILURE:{stage.value}"):
        PostgresPaperStore(DATABASE_URL).commit(write, _fail_after=stage)

    with psycopg.connect(DATABASE_URL) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM paper_command_receipts WHERE account_id=%s),
              (SELECT count(*) FROM paper_authorization_attempts WHERE account_id=%s),
              (SELECT count(*) FROM paper_broker_inputs WHERE account_id=%s),
              (SELECT count(*) FROM paper_orders WHERE account_id=%s),
              (SELECT count(*) FROM paper_fills f JOIN paper_orders o USING(order_id)
               WHERE o.account_id=%s),
              (SELECT count(*) FROM paper_observation_effects WHERE account_id=%s),
              (SELECT count(*) FROM paper_inventory_lots WHERE account_id=%s),
              (SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),
              (SELECT count(*) FROM outbox_events WHERE aggregate_id=%s)
            """,
            (write.account_id,) * 8 + (write.order.order_id,),
        ).fetchone()
    assert counts == (0, 0, 0, 0, 0, 0, 0, 0, 0)
