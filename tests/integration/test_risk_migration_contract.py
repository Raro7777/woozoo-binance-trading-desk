from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import psycopg

from docker_infrastructure_lock import docker_infrastructure_lock
from paper_engine.persistence import PostgresPaperStore
from risk_engine import KillActivation, PostgresKillSwitch
from test_paper_postgres_persistence import complete_write


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260719_0005_risk_engine.py"
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def normalized_phase4_function_digests(
    connection: psycopg.Connection[object],
) -> tuple[str, str]:
    definitions = connection.execute(
        "SELECT pg_get_functiondef(to_regprocedure(%s)),pg_get_functiondef(to_regprocedure(%s))",
        (
            "append_paper_outbox(character varying,character varying,jsonb,character varying,"
            "timestamp with time zone,character varying,character varying,bigint)",
            "assert_paper_relational_consistency()",
        ),
    ).fetchone()
    assert definitions is not None and all(definition is not None for definition in definitions)
    return tuple(
        hashlib.sha256(" ".join(definition.split()).encode()).hexdigest()
        for definition in definitions
    )


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
    assert "GRANT INSERT ON risk_decisions" not in text
    assert "GRANT INSERT ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "GRANT DELETE ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "RECOVERY" not in text
    for forbidden in ("api_key", "signature", "testnet", "mainnet", "approval"):
        assert forbidden not in text.lower()


def test_phase_four_to_five_to_four_to_five_migration_cycle_is_recoverable() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "20260719_0004")
            with psycopg.connect(DATABASE_URL) as connection:
                phase4_function_digests = normalized_phase4_function_digests(connection)
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
                assert normalized_phase4_function_digests(connection) == phase4_function_digests
            assert store.semantic_digest(paper_write.account_id) == baseline_digest

            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("""
                    CREATE OR REPLACE FUNCTION paper_lock_kill_barrier()
                    RETURNS TABLE(active boolean, version bigint,
                                  last_activation_event_id varchar) AS $$
                      SELECT false,0::bigint,NULL::varchar
                    $$ LANGUAGE sql
                """)
            post_downgrade_write = complete_write(suffix="p4-after-p5-downgrade")
            store.commit(post_downgrade_write)
            post_downgrade_digest = store.semantic_digest(post_downgrade_write.account_id)
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("DROP FUNCTION paper_lock_kill_barrier()")
                assert normalized_phase4_function_digests(connection) == phase4_function_digests
                assert connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),"
                    "(SELECT count(*) FROM paper_outbox_events_v1 WHERE account_id=%s),"
                    "(SELECT count(*) FROM ("
                    " SELECT entry.transaction_id,entry.commodity "
                    " FROM paper_ledger_entries entry "
                    " JOIN paper_ledger_transactions tx USING(transaction_id) "
                    " WHERE tx.account_id=%s GROUP BY entry.transaction_id,entry.commodity "
                    " HAVING sum(entry.debit)<>sum(entry.credit)"
                    ") imbalance)",
                    (post_downgrade_write.account_id,) * 3,
                ).fetchone() == (4, 2, 0)
            assert len(post_downgrade_digest) == 64
            assert store.semantic_digest(post_downgrade_write.account_id) == post_downgrade_digest

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
                assert connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s),"
                    "(SELECT count(*) FROM paper_outbox_events_v1 WHERE account_id=%s),"
                    "(SELECT count(*) FROM ("
                    " SELECT entry.transaction_id,entry.commodity "
                    " FROM paper_ledger_entries entry "
                    " JOIN paper_ledger_transactions tx USING(transaction_id) "
                    " WHERE tx.account_id=%s GROUP BY entry.transaction_id,entry.commodity "
                    " HAVING sum(entry.debit)<>sum(entry.credit)"
                    ") imbalance)",
                    (post_downgrade_write.account_id,) * 3,
                ).fetchone() == (4, 2, 0)
            assert store.semantic_digest(paper_write.account_id) == baseline_digest
            assert store.semantic_digest(post_downgrade_write.account_id) == post_downgrade_digest
        finally:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            run("docker", "compose", "down", "-v")


def test_preexisting_risk_login_and_direct_grants_survive_empty_downgrade() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "20260719_0004")
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("CREATE ROLE woozoo_risk_engine LOGIN CONNECTION LIMIT 7")
                connection.execute("GRANT USAGE ON SCHEMA public TO woozoo_risk_engine")
                connection.execute("GRANT SELECT ON outbox_events TO woozoo_risk_engine")
            run(sys.executable, "-m", "alembic", "upgrade", "20260719_0005")
            run(sys.executable, "-m", "alembic", "downgrade", "20260719_0004")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT rolcanlogin,rolconnlimit FROM pg_roles "
                    "WHERE rolname='woozoo_risk_engine'"
                ).fetchone() == (True, 7)
                assert connection.execute(
                    "SELECT has_schema_privilege('woozoo_risk_engine','public','USAGE'),"
                    "has_table_privilege('woozoo_risk_engine','outbox_events','SELECT'),"
                    "has_table_privilege('woozoo_risk_engine','outbox_events','INSERT'),"
                    "has_table_privilege('woozoo_risk_engine',"
                    "'paper_reconciliation_checkpoints','SELECT')"
                ).fetchone() == (True, True, False, False)
        finally:
            run("docker", "compose", "down", "-v")


def test_phase_five_downgrade_fails_closed_when_immutable_history_exists() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            activation = PostgresKillSwitch(DATABASE_URL).activate(
                KillActivation(
                    request_id="migration-downgrade-guard",
                    expected_version=0,
                    trigger_kind="MANUAL",
                    actor_id="operator:migration-test",
                    reason_code="MANUAL_SAFETY_STOP",
                    reason="preserve immutable Phase 5 history",
                    observed_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
                    context_digest="a" * 64,
                )
            )

            result = subprocess.run(
                [sys.executable, "-m", "alembic", "downgrade", "20260719_0004"],
                cwd=ROOT,
                check=False,
                env={**os.environ, **ENVIRONMENT},
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0
            assert "Phase 5 downgrade blocked: immutable Risk/Kill history exists" in (
                result.stdout + result.stderr
            )
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                    "20260719_0005",
                )
                assert connection.execute(
                    "SELECT active,version,last_activation_event_id FROM kill_switch_state "
                    "WHERE scope='paper-global'"
                ).fetchone() == (True, 1, activation.activation_event_id)
        finally:
            run("docker", "compose", "down", "-v")
