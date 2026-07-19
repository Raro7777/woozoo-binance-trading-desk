from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

import psycopg

from paper_engine.persistence import PostgresPaperStore
from test_paper_postgres_persistence import complete_write


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260719_0005_risk_engine.py"
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}


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


def test_risk_migration_001_closes_authority_and_barrier_boundaries() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "risk_input_digest",
        "unique=True",
        "risk_decisions",
        "risk_outbox_links",
        "kill_switch_events",
        "kill_switch_state",
        "paper-global",
        "paper_lock_kill_barrier",
        "FOR SHARE",
        "SECURITY DEFINER SET search_path = pg_catalog, public",
        "REVOKE ALL ON FUNCTION paper_lock_kill_barrier() FROM PUBLIC",
        "enforce_kill_state_activation_only",
        "Kill state permits monotonic activation only",
        "assert_kill_activation_consistency",
        "Kill activation transaction is incomplete",
        "paper_kill_inbox",
        "paper_kill_cancel_batches",
        "paper_kill_cancel_items",
        "cancelled_count BETWEEN 1 AND 100",
        "woozoo_risk_engine",
    ):
        assert required in text
    assert "GRANT UPDATE ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "GRANT INSERT ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "GRANT DELETE ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "RECOVERY" not in text
    for forbidden in ("api_key", "signature", "testnet", "mainnet", "approval"):
        assert forbidden not in text.lower()


def test_phase_four_to_five_to_four_to_five_migration_cycle_is_recoverable() -> None:
    with infrastructure_lock():
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            run(sys.executable, "-m", "alembic", "downgrade", "20260719_0004")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT to_regclass('paper_orders')").fetchone() == (
                    "paper_orders",
                )
                assert connection.execute("SELECT to_regclass('kill_switch_state')").fetchone() == (
                    None,
                )
                connection.execute("""
                    CREATE OR REPLACE FUNCTION paper_lock_kill_barrier()
                    RETURNS TABLE(active boolean, version bigint,
                                  last_activation_event_id varchar) AS $$
                      SELECT false,0::bigint,NULL::varchar
                    $$ LANGUAGE sql
                """)

            paper_write = complete_write(suffix="p4-p5-cycle-authority")
            store = PostgresPaperStore(DATABASE_URL)
            store.commit(paper_write)
            baseline_digest = store.semantic_digest(paper_write.account_id)
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("DROP FUNCTION paper_lock_kill_barrier()")
                phase4_authority = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM paper_accounts WHERE account_id=%s),"
                    "(SELECT count(*) FROM paper_orders WHERE account_id=%s),"
                    "(SELECT count(*) FROM paper_asset_balances "
                    " WHERE account_id=%s AND held>0),"
                    "(SELECT count(*) FROM paper_ledger_transactions "
                    " WHERE account_id=%s AND business_event_type='paper.hold'),"
                    "(SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),"
                    "(SELECT count(*) FROM paper_outbox_events_v1 WHERE account_id=%s),"
                    "to_regprocedure('append_paper_outbox(varchar,varchar,jsonb,varchar,"
                    "timestamptz,varchar,varchar,bigint)') IS NOT NULL,"
                    "has_table_privilege('woozoo_paper_engine','paper_orders','SELECT'),"
                    "has_column_privilege('woozoo_paper_engine','paper_orders',"
                    "'filled_quantity','UPDATE')",
                    (paper_write.account_id,) * 6,
                ).fetchone()
            assert phase4_authority == (1, 1, 1, 1, 4, 2, True, True, True)

            run(sys.executable, "-m", "alembic", "upgrade", "20260719_0005")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
                ).fetchone() == (False, 0)
                assert connection.execute(
                    "SELECT to_regprocedure('paper_lock_kill_barrier()') IS NOT NULL,"
                    "has_function_privilege('woozoo_paper_engine',"
                    "'paper_lock_kill_barrier()','EXECUTE')"
                ).fetchone() == (True, True)
            assert store.semantic_digest(paper_write.account_id) == baseline_digest

            run(sys.executable, "-m", "alembic", "downgrade", "20260719_0004")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT to_regclass('kill_switch_state')").fetchone() == (
                    None,
                )
                assert connection.execute(
                    "SELECT count(*) FROM outbox_events WHERE payload->>'producer'='risk-engine'"
                ).fetchone() == (0,)
                assert (
                    connection.execute(
                        "SELECT to_regprocedure('append_paper_outbox(varchar,varchar,jsonb,varchar,"
                        "timestamptz,varchar,varchar,bigint)') IS NOT NULL,"
                        "has_table_privilege('woozoo_paper_engine','paper_orders','SELECT'),"
                        "has_column_privilege('woozoo_paper_engine','paper_orders',"
                        "'filled_quantity','UPDATE')"
                    ).fetchone()
                    == phase4_authority[6:]
                )
            assert store.semantic_digest(paper_write.account_id) == baseline_digest

            run(sys.executable, "-m", "alembic", "upgrade", "20260719_0005")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
                ).fetchone() == (False, 0)
                assert connection.execute(
                    "SELECT has_function_privilege('woozoo_paper_engine',"
                    "'paper_lock_kill_barrier()','EXECUTE')"
                ).fetchone() == (True,)
                assert (
                    connection.execute(
                        "SELECT (SELECT count(*) FROM paper_accounts WHERE account_id=%s),"
                        "(SELECT count(*) FROM paper_orders WHERE account_id=%s),"
                        "(SELECT count(*) FROM paper_asset_balances "
                        " WHERE account_id=%s AND held>0),"
                        "(SELECT count(*) FROM paper_ledger_transactions "
                        " WHERE account_id=%s AND business_event_type='paper.hold'),"
                        "(SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),"
                        "(SELECT count(*) FROM paper_outbox_events_v1 WHERE account_id=%s)",
                        (paper_write.account_id,) * 6,
                    ).fetchone()
                    == phase4_authority[:6]
                )
            assert store.semantic_digest(paper_write.account_id) == baseline_digest
        finally:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            run("docker", "compose", "down", "-v")
