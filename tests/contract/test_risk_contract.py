import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_risk_contract_001_is_closed_typed_and_dormant() -> None:
    spec = ROOT / "packages/contracts/spec"
    risk_input = json.loads((spec / "risk-input.v1.json").read_text("utf-8"))
    decision = json.loads((spec / "risk-decision.v1.json").read_text("utf-8"))
    kill = json.loads((spec / "kill-switch.v1.json").read_text("utf-8"))
    events = json.loads((spec / "risk-domain-events.v1.json").read_text("utf-8"))
    openapi = json.loads((spec / "openapi.v1.json").read_text("utf-8"))

    assert risk_input["additionalProperties"] is False
    assert all(
        value.get("additionalProperties") is False
        for value in risk_input["$defs"].values()
        if value.get("type") == "object"
    )
    assert "ALLOW_UNSAFE" not in decision["$defs"]["reasonCode"]["enum"]
    assert decision["$defs"]["reasonCode"]["enum"][-1] == "RISK_ALLOWED"
    assert {
        "actor_id",
        "trigger_kind",
        "reason",
        "reason_code",
        "observed_at",
        "context_digest",
        "prior_version",
        "version",
    }.issubset(kill["required"])
    assert "DRAWDOWN_LIMIT_EXCEEDED" not in kill["properties"]["reason_code"]["enum"]
    assert events["x-activation-phase"] == 7
    assert len(events["oneOf"]) == 2
    paths = set(openapi["paths"])
    assert any("risk" in path for path in paths)
    assert any("kill" in path for path in paths)
    assert any("approval" in path for path in paths)
    assert not any("authorization" in path or "/internal/" in path for path in paths)
