from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

import psycopg
import pytest

from docker_infrastructure_lock import docker_infrastructure_lock
from risk_engine import KillActivation, PostgresKillSwitch


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
ATTEMPTS = ("timer", "ai", "process_restart", "redis_expiry", "unauthenticated_actor")


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


@pytest.fixture(scope="module")
def active_kill() -> Iterator[tuple[str, int]]:
    with docker_infrastructure_lock():
        run("docker", "compose", "up", "-d", "--wait", "postgres", "redis")
        run(sys.executable, "-m", "alembic", "upgrade", "head")
        result = PostgresKillSwitch(DATABASE_URL).activate(
            KillActivation(
                request_id="kill-002-active",
                expected_version=0,
                trigger_kind="MANUAL",
                actor_id="operator:safety-lead",
                reason_code="MANUAL_SAFETY_STOP",
                reason="recovery boundary fixture",
                observed_at=datetime(2026, 7, 19, 15, 0, tzinfo=UTC),
                context_digest="b" * 64,
            )
        )
        try:
            yield result.activation_event_id, result.version
        finally:
            run("docker", "compose", "down", "-v")


def assert_active(expected: tuple[str, int]) -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        state = connection.execute(
            "SELECT active,version,last_activation_event_id FROM kill_switch_state "
            "WHERE scope='paper-global'"
        ).fetchone()
        off_events = connection.execute(
            "SELECT count(*) FROM outbox_events WHERE event_type LIKE 'kill-switch.%' "
            "AND payload->'data'->>'active'='false'"
        ).fetchone()
    assert state == (True, expected[1], expected[0])
    assert off_events == (0,)


def test_kill_002_has_no_automatic_ai_or_unauthenticated_recovery() -> None:
    kill_source = (ROOT / "services/risk-engine/src/risk_engine/kill_switch.py").read_text(
        encoding="utf-8"
    )
    migration = (ROOT / "db/migrations/versions/20260719_0005_risk_engine.py").read_text(
        encoding="utf-8"
    )
    combined = (kill_source + migration).lower()
    for forbidden in (
        "def recover",
        "recovery-confirmed",
        "recovery_confirmed",
        "auto_recover",
        "redis expiry",
        "timer trigger",
        "ai actor",
    ):
        assert forbidden not in combined

    with pytest.raises(ValueError, match="UNAUTHENTICATED_KILL_ACTOR"):
        KillActivation(
            request_id="manual-1",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="anonymous",
            reason_code="MANUAL_SAFETY_STOP",
            reason="stop",
            observed_at=datetime.now(UTC),
            context_digest="a" * 64,
        ).validate()


def test_kill_002_scans_every_executable_config_and_tool_registry_for_recovery_writer() -> None:
    roots = ["services", "packages", "apps", "scripts", "infra", "db/migrations", ".codex/agents"]
    forbidden = (
        "deactivate_kill_switch",
        "recover_kill_switch",
        "kill-switch.recovery-confirmed",
        "set active=false",
        "set active = false",
    )
    matches: list[str] = []
    for root_name in roots:
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or any(part in {"node_modules", ".next"} for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8").lower()
            except UnicodeDecodeError:
                continue
            for token in forbidden:
                if token in content:
                    matches.append(f"{path.relative_to(ROOT)}:{token}")
    phase_state = json.loads(
        (ROOT / "docs/woozoo-trading-desk/phase-state.json").read_text(encoding="utf-8")
    )
    assert phase_state["current_phase"] == 7
    assert sorted(matches) == sorted(
        [
            "services\\control-api\\src\\control_api\\command_ports.py:recover_kill_switch",
            "db\\migrations\\versions\\20260720_0007_trading_room.py:recover_kill_switch",
            "db\\migrations\\versions\\20260720_0007_trading_room.py:set active=false",
        ]
    )
    migration = (ROOT / "db/migrations/versions/20260720_0007_trading_room.py").read_text(
        encoding="utf-8"
    )
    assert "automatic Kill recovery is forbidden" in migration
    assert "p_actor_id varchar,p_session_digest varchar,p_csrf_digest varchar" in migration
    assert "GRANT EXECUTE ON FUNCTION recover_kill_switch_v1" in migration
    assert "REVOKE ALL ON FUNCTION recover_kill_switch_v1" in migration


@pytest.mark.parametrize("attempt", ATTEMPTS, ids=ATTEMPTS)
def test_kill_002_attempt_keeps_postgres_active(attempt: str, active_kill: tuple[str, int]) -> None:
    if attempt == "timer":
        time.sleep(0.01)
    elif attempt == "ai":
        with pytest.raises(ValueError, match="UNAUTHENTICATED_KILL_ACTOR"):
            KillActivation(
                request_id="ai-recover",
                expected_version=active_kill[1],
                trigger_kind="MANUAL",
                actor_id="ai:risk-agent",
                reason_code="MANUAL_SAFETY_STOP",
                reason="attempted recovery",
                observed_at=datetime.now(UTC),
                context_digest="c" * 64,
            ).validate()
    elif attempt == "process_restart":
        run("docker", "compose", "restart", "postgres")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
    elif attempt == "redis_expiry":
        run("docker", "compose", "stop", "redis")
        run("docker", "compose", "up", "-d", "--wait", "redis")
    elif attempt == "unauthenticated_actor":
        with pytest.raises(ValueError, match="UNAUTHENTICATED_KILL_ACTOR"):
            KillActivation(
                request_id="anonymous-recover",
                expected_version=active_kill[1],
                trigger_kind="MANUAL",
                actor_id="anonymous",
                reason_code="MANUAL_SAFETY_STOP",
                reason="attempted recovery",
                observed_at=datetime.now(UTC),
                context_digest="d" * 64,
            ).validate()
    else:
        raise AssertionError(attempt)
    assert_active(active_kill)
