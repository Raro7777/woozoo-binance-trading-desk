from pathlib import Path
import re


ROOT = Path(__file__).parents[2]


def test_phase_five_has_no_active_risk_approval_or_authorization_ingress() -> None:
    openapi = (ROOT / "packages/contracts/spec/openapi.v1.json").read_text("utf-8").lower()
    app = (ROOT / "services/control-api/src/control_api/app.py").read_text("utf-8").lower()
    for forbidden_path in ("risk-evaluations", "kill-switch", "paper-approvals", "paper-authorizations"):
        assert forbidden_path not in openapi
        assert forbidden_path not in app


def test_risk_engine_has_no_network_exchange_secret_or_ai_capability() -> None:
    directory = ROOT / "services/risk-engine/src"
    if not directory.exists():
        return
    sources = "\n".join(path.read_text("utf-8") for path in directory.rglob("*.py")).lower()
    for forbidden in (
        "fastapi", "httpx", "websocket", "requests", "binance", "api_key",
        "api_secret", "paperexecutionauthorization", "llm", "openai",
    ):
        assert re.search(rf"(?<![a-z0-9_]){re.escape(forbidden)}(?![a-z0-9_])", sources) is None


def test_phase_five_does_not_create_approval_or_authorization_contracts() -> None:
    names = {path.name.lower() for path in (ROOT / "packages/contracts/spec").iterdir()}
    assert "paper-approval.v1.json" not in names
    assert "paper-execution-authorization.v1.json" not in names
