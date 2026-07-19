from __future__ import annotations

from pathlib import Path

from control_api.app import create_app
from control_api.dependencies import DependencySnapshot, StaticHealthProbe
from evidence_worker.app import create_evidence_app


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = {
    "TRADING_MODE": "paper",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
    "EVIDENCE_DATABASE_URL": "postgresql://woozoo_evidence_writer@127.0.0.1:5433/woozoo",
    "REDIS_URL": "redis://127.0.0.1:6380/0",
}


def test_evidence_worker_has_no_network_or_later_phase_capability() -> None:
    source_root = ROOT / "services" / "evidence-worker" / "src"
    sources = "\n".join(path.read_text("utf-8") for path in source_root.rglob("*.py"))
    forbidden = (
        "httpx",
        "requests",
        "websockets",
        "urllib",
        "socket",
        "binance.com",
        "api.binance",
        "data-api.binance",
        "data-stream.binance",
        "X-MBX-APIKEY",
        "create_order",
        "cancel_order",
        "trade_proposal",
        "risk_decision",
        "paper_order",
    )
    lowered = sources.lower()
    assert all(value.lower() not in lowered for value in forbidden)


def test_phase_three_registers_only_the_approved_evidence_command_route() -> None:
    app = create_app(
        ENVIRONMENT,
        StaticHealthProbe(DependencySnapshot("healthy", "healthy")),
    )
    evidence_routes = [
        (method, route.path)
        for route in app.routes
        if route.path.startswith("/api/v1/evidence")
        for method in route.methods
    ]
    assert evidence_routes == [("GET", "/api/v1/evidence/{evidence_id}")]
    command_app = create_evidence_app(ENVIRONMENT)
    command_routes = [
        (method, route.path)
        for route in command_app.routes
        if route.path.startswith("/api/v1/commands")
        for method in route.methods
    ]
    assert command_routes == [("POST", "/api/v1/commands/evidence-snapshots")]
