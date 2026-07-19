from pathlib import Path

import pytest

from paper_engine.settings import PaperSettings


ROOT = Path(__file__).parents[2]


def test_phase_four_has_no_active_paper_route_or_network_ingress() -> None:
    app = (ROOT / "services/control-api/src/control_api/app.py").read_text(encoding="utf-8")
    openapi = (ROOT / "packages/contracts/spec/openapi.v1.json").read_text(encoding="utf-8")
    paper_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "services/paper-engine/src").rglob("*.py")
    ).lower()
    assert "/paper/" not in app
    assert "/paper/" not in openapi
    for forbidden in ("fastapi", "httpx", "websocket", "requests", "binance"):
        assert forbidden not in paper_sources


@pytest.mark.parametrize("mode", [None, "", "live", "testnet", "PAPER"])
def test_phase_four_settings_fail_closed_outside_exact_paper(mode: str | None) -> None:
    environment = {"PAPER_DATABASE_URL": "postgresql://local/paper"}
    if mode is not None:
        environment["TRADING_MODE"] = mode
    with pytest.raises(ValueError, match="TRADING_MODE"):
        PaperSettings.load(environment)


def test_phase_four_settings_reject_credential_vocabulary() -> None:
    with pytest.raises(ValueError, match="credential"):
        PaperSettings.load(
            {
                "TRADING_MODE": "paper",
                "PAPER_DATABASE_URL": "postgresql://local/paper",
                "EXCHANGE_API_KEY": "forbidden-canary",
            }
        )
