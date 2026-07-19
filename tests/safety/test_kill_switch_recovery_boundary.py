from datetime import UTC, datetime
from pathlib import Path

import pytest

from risk_engine import KillActivation


ROOT = Path(__file__).resolve().parents[2]


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
