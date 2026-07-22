import json
from pathlib import Path
import re


ROOT = Path(__file__).parents[2]


def test_phase_five_dormancy_is_activated_only_at_the_phase_seven_browser_boundary() -> None:
    openapi = json.loads((ROOT / "packages/contracts/spec/openapi.v1.json").read_text("utf-8"))
    paths = set(openapi["paths"])
    assert {
        "/api/v1/session/login",
        "/api/v1/session",
        "/api/v1/analysis-runs",
        "/api/v1/paper-approvals",
        "/api/v1/kill-switch",
    } <= paths
    assert not any("/internal/" in path or "paper-authorizations" in path for path in paths)


def test_risk_engine_has_no_network_exchange_secret_or_ai_capability() -> None:
    directory = ROOT / "services/risk-engine/src"
    if not directory.exists():
        return
    sources = "\n".join(path.read_text("utf-8") for path in directory.rglob("*.py")).lower()
    for forbidden in (
        "fastapi",
        "httpx",
        "websocket",
        "requests",
        "binance",
        "api_key",
        "api_secret",
        "paperexecutionauthorization",
        "llm",
        "openai",
    ):
        assert re.search(rf"(?<![a-z0-9_]){re.escape(forbidden)}(?![a-z0-9_])", sources) is None


def test_phase_seven_contracts_preserve_the_phase_five_dormant_boundary() -> None:
    for name in ("paper-approval.v1.json", "paper-execution-authorization.v1.json"):
        schema = json.loads((ROOT / "packages/contracts/spec" / name).read_text("utf-8"))
        assert schema["x-creation-phase"] == 7
        assert schema["x-activation-phase"] == 7
