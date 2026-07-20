from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys

import psycopg
import pytest

from docker_infrastructure_lock import docker_infrastructure_lock
from paper_engine.persistence import PostgresPaperStore
from risk_engine import PostgresReconciliationKillHandler, canonical_hash
from test_paper_postgres_persistence import complete_write


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
RISK_WRITER_URL = "postgresql://woozoo_risk_engine@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
NOW = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def test_critical_reconciliation_mismatch_activates_kill_once_and_retries_idempotently() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            write = complete_write(suffix="critical-reconciliation-kill")
            store = PostgresPaperStore(DATABASE_URL)
            store.commit(write)
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("SET session_replication_role='replica'")
                connection.execute(
                    "UPDATE paper_asset_balances SET available=available+1 "
                    "WHERE account_id=%s AND asset='USDT'",
                    (write.account_id,),
                )
                connection.execute("SET session_replication_role='origin'")

            checkpoint_id = "checkpoint-critical-reconciliation-kill"
            checkpoint = store.reconcile(
                write.account_id, checkpoint_id=checkpoint_id, created_at=NOW
            )
            assert checkpoint.status == "FAILED"
            assert "PHYSICAL_LEDGER_MISMATCH" in checkpoint.mismatch_codes

            handler = PostgresReconciliationKillHandler(RISK_WRITER_URL)
            first = handler.handle(checkpoint_id)
            assert first.activation is not None
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute(
                    "UPDATE outbox_events SET published_at=%s WHERE event_id=%s",
                    (NOW, first.activation.outbox_event_id),
                )
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute(
                    "UPDATE outbox_events SET payload=jsonb_set(payload,'{producer}','null') "
                    "WHERE event_id=%s",
                    (first.activation.outbox_event_id,),
                )
                with pytest.raises(
                    psycopg.errors.RaiseException,
                    match="activation transaction is incomplete",
                ):
                    connection.commit()
                connection.rollback()
            second = handler.handle(checkpoint_id)
            assert first.reason_code == "PHYSICAL_LEDGER_MISMATCH"
            assert first.activation.created is True
            assert second.activation is not None and second.activation.created is False
            assert second.activation.activation_event_id == first.activation.activation_event_id

            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT active,version,last_activation_event_id FROM kill_switch_state "
                    "WHERE scope='paper-global'"
                ).fetchone() == (True, 1, first.activation.activation_event_id)
                assert connection.execute(
                    "SELECT count(*) FROM risk_kill_command_receipts"
                ).fetchone() == (1,)
                event = connection.execute(
                    "SELECT trigger_kind,actor_id,reason_code,context_digest "
                    "FROM kill_switch_events WHERE activation_event_id=%s",
                    (first.activation.activation_event_id,),
                ).fetchone()
                assert event is not None
                assert event[:3] == (
                    "INVARIANT",
                    "safety-service:reconciliation",
                    "PHYSICAL_LEDGER_MISMATCH",
                )
                assert event[3] == canonical_hash(
                    {
                        "account_id": write.account_id,
                        "checkpoint_id": checkpoint_id,
                        "input_digest": checkpoint.input_digest,
                        "mismatch_codes": list(checkpoint.mismatch_codes),
                        "output_digest": checkpoint.output_digest,
                    }
                )
        finally:
            run("docker", "compose", "down", "-v")
