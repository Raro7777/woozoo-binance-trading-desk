import hashlib
import json
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


def test_public_symbol_rule_projection_is_hash_bound_and_derives_adopted_rules() -> None:
    directory = ROOT / "docs/woozoo-trading-desk/phase-4"
    metadata = json.loads(
        (directory / "binance-public-symbol-rule-reverification.json").read_text("utf-8")
    )
    projection_bytes = (directory / metadata["deterministic_projection"]).read_bytes()
    assert (
        hashlib.sha256(projection_bytes).hexdigest() == metadata["deterministic_projection_sha256"]
    )
    projection = json.loads(projection_bytes)
    derived = {
        symbol["symbol"]: {
            "status": symbol["status"],
            "base_asset": symbol["baseAsset"],
            "quote_asset": symbol["quoteAsset"],
            "tick_size": symbol["filters"]["PRICE_FILTER"]["tickSize"],
            "step_size": symbol["filters"]["LOT_SIZE"]["stepSize"],
            "min_quantity": symbol["filters"]["LOT_SIZE"]["minQty"],
            "min_notional": symbol["filters"]["NOTIONAL"]["minNotional"],
        }
        for symbol in projection["symbols"]
    }
    assert derived == metadata["adopted_rules"]
