from __future__ import annotations

from pathlib import Path

import pytest

from spot_testnet_gateway.capabilities import Capability, GatewayRequest, validate_request


ROOT = Path(__file__).resolve().parents[2]


def test_gateway_capability_rejects_extra_query_fields_and_non_testnet_shapes() -> None:
    base = (
        ("symbol", "BTCUSDT"),
        ("side", "BUY"),
        ("type", "LIMIT"),
        ("timeInForce", "GTC"),
        ("quantity", "0.001"),
        ("price", "60000.00"),
        ("newClientOrderId", "wz8-" + "a" * 32),
        ("recvWindow", "5000"),
        ("timestamp", "1655979030000"),
    )
    validate_request(GatewayRequest(Capability.SUBMIT_LIMIT_GTC, "POST", "/api/v3/order", base))
    for parameters in (
        base + (("quoteOrderQty", "1"),),
        tuple((name, "MARKET") if name == "type" else (name, value) for name, value in base),
        tuple((name, "IOC") if name == "timeInForce" else (name, value) for name, value in base),
        tuple((name, "9999") if name == "recvWindow" else (name, value) for name, value in base),
    ):
        with pytest.raises(ValueError, match="CAPABILITY_PARAMETERS_MISMATCH"):
            validate_request(
                GatewayRequest(Capability.SUBMIT_LIMIT_GTC, "POST", "/api/v3/order", parameters)
            )


def test_control_browser_and_ai_packages_do_not_import_gateway_or_secret_surface() -> None:
    forbidden_roots = (
        ROOT / "services" / "control-api",
        ROOT / "services" / "agent-orchestrator",
        ROOT / "apps" / "trading-room-web" / "src",
    )
    forbidden_markers = (
        "spot_testnet_gateway",
        "testnet.binance.vision",
        "SPOT_TESTNET_API_KEY_FILE",
        "SPOT_TESTNET_SIGNING_SECRET_FILE",
        "BINANCE_API_KEY",
        "BINANCE_SECRET_KEY",
    )
    for root in forbidden_roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".js", ".mjs"}:
                continue
            text = path.read_text("utf-8")
            for marker in forbidden_markers:
                assert marker not in text, (path, marker)


def test_gateway_package_contains_no_forbidden_fallback_or_capability_vocabulary() -> None:
    root = ROOT / "services" / "spot-testnet-gateway"
    text = "\n".join(
        path.read_text("utf-8") for path in root.rglob("*.py") if path.is_file()
    ).lower()
    for forbidden in (
        "api.binance.com",
        "/sapi/",
        "cancelreplace",
        "quoteorderqty",
        "market_order",
        "withdraw",
        "futures",
        "margin",
        "leverage",
    ):
        assert forbidden not in text


def test_testnet_runtime_can_only_clear_the_bootstrap_default_off_barrier() -> None:
    execution_root = ROOT / "services" / "testnet-execution-service" / "src" / "testnet_execution"
    writers: list[tuple[Path, str]] = []
    for path in execution_root.rglob("*.py"):
        text = path.read_text("utf-8")
        for statement in text.split("connection.execute("):
            if "active=false" in statement[:500]:
                writers.append((path, statement[:500]))

    assert len(writers) == 2
    for path, statement in writers:
        assert "reason_code='DEFAULT_OFF'" in statement, path
