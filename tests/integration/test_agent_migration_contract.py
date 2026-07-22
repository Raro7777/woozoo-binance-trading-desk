from pathlib import Path
import os
import subprocess
import sys

import psycopg

from docker_infrastructure_lock import docker_infrastructure_lock


ROOT = Path(__file__).parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"DATABASE_URL": DATABASE_URL, "TRADING_MODE": "paper"}


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def test_phase6_migration_is_append_only_least_privilege_and_test_only() -> None:
    source = (ROOT / "db/migrations/versions/20260720_0006_agent_orchestrator.py").read_text(
        "utf-8"
    )
    for table in (
        "agent_prompt_manifests",
        "analysis_runs",
        "agent_reports",
        "agent_report_evidence_refs",
        "trade_proposals",
        "trade_proposal_evidence_refs",
        "analysis_audit_records",
        "analysis_run_events",
        "agent_outbox_links",
    ):
        assert f'"{table}"' in source
    assert "reject_agent_history_mutation" in source
    assert "namespace='test'" in source
    assert "fk_risk_decisions_phase6_proposal" in source
    assert "evidence_reader_v1" in source
    assert "GRANT SELECT ON risk_decisions" not in source
    assert "GRANT SELECT ON paper_orders" not in source
    assert "immutable analysis history exists; downgrade refused" in source


def test_preexisting_agent_role_and_direct_grants_survive_empty_downgrade() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "20260719_0005")
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("CREATE ROLE woozoo_agent_orchestrator LOGIN CONNECTION LIMIT 7")
                connection.execute("GRANT USAGE ON SCHEMA public TO woozoo_agent_orchestrator")
                connection.execute(
                    "GRANT SELECT ON evidence_reader_v1, outbox_events TO woozoo_agent_orchestrator"
                )
            run(sys.executable, "-m", "alembic", "upgrade", "20260720_0006")
            run(sys.executable, "-m", "alembic", "downgrade", "20260719_0005")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT rolcanlogin,rolconnlimit FROM pg_roles "
                    "WHERE rolname='woozoo_agent_orchestrator'"
                ).fetchone() == (True, 7)
                assert connection.execute(
                    "SELECT has_schema_privilege('woozoo_agent_orchestrator','public','USAGE'),"
                    "has_table_privilege('woozoo_agent_orchestrator','evidence_reader_v1','SELECT'),"
                    "has_table_privilege('woozoo_agent_orchestrator','evidence_candle_reader_v1','SELECT'),"
                    "has_table_privilege('woozoo_agent_orchestrator','evidence_feature_reader_v1','SELECT'),"
                    "has_table_privilege('woozoo_agent_orchestrator','outbox_events','SELECT'),"
                    "has_table_privilege('woozoo_agent_orchestrator','outbox_events','INSERT')"
                ).fetchone() == (True, True, False, False, True, False)
        finally:
            run("docker", "compose", "down", "-v")


def test_empty_downgrade_removes_migration_created_agent_role() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "20260720_0006")
            run(sys.executable, "-m", "alembic", "downgrade", "20260719_0005")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM pg_roles WHERE rolname='woozoo_agent_orchestrator'"
                ).fetchone() == (0,)
        finally:
            run("docker", "compose", "down", "-v")
