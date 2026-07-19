"""The deliberately small Phase 1 health API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from platform_core.config import PlatformSettings
from platform_core.generated_contracts import API_VERSION, HEALTH_PATH
from platform_core.primitives import new_request_id, utc_now
from platform_core.redaction import redact

from .dependencies import DependencySnapshot, HealthProbe, PlatformHealthProbe
from .market_projection import (
    MarketProjectionUnavailable,
    MarketStatusProjection,
    PostgresMarketStatusProjection,
)


class ContractBoundFastAPI(FastAPI):
    """Prevents FastAPI's inferred schema from becoming a second API contract."""

    def openapi(self) -> dict[str, Any]:
        raise RuntimeError(
            "FastAPI OpenAPI is disabled; packages/contracts/spec/openapi.v1.json is authoritative"
        )


def _base_envelope() -> dict[str, object]:
    request_id = str(new_request_id())
    return {
        "api_version": API_VERSION,
        "request_id": request_id,
        "correlation_id": request_id,
        "served_at": utc_now().isoformat().replace("+00:00", "Z"),
    }


def _dependency_unavailable(snapshot: DependencySnapshot) -> JSONResponse:
    envelope = _base_envelope()
    envelope["error"] = {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "a required platform dependency is unavailable",
    }
    envelope["meta"] = {"resource_version": None, "next_cursor": None}
    return JSONResponse(redact(envelope), status_code=503)


def _market_error(code: str, message: str, status_code: int) -> JSONResponse:
    envelope = _base_envelope()
    envelope["error"] = {"code": code, "message": message}
    envelope["meta"] = {"resource_version": None, "next_cursor": None}
    return JSONResponse(redact(envelope), status_code=status_code)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def create_app(
    environment: Mapping[str, str] | None = None,
    probe: HealthProbe | None = None,
    market_projection: MarketStatusProjection | None = None,
) -> FastAPI:
    values = os.environ if environment is None else environment
    settings = PlatformSettings.from_mapping(values).require_service_dependencies()
    health_probe = probe or PlatformHealthProbe()
    assert settings.database_url is not None
    market_status = market_projection or PostgresMarketStatusProjection(settings.database_url)
    app = ContractBoundFastAPI(docs_url=None, openapi_url=None, redoc_url=None)

    @app.get(HEALTH_PATH)
    def get_platform_health() -> JSONResponse:
        snapshot = health_probe.snapshot(settings)
        if snapshot.postgres != "healthy":
            return _dependency_unavailable(snapshot)

        envelope = _base_envelope()
        envelope["data"] = {
            "service": "control-api",
            "status": "healthy" if snapshot.redis == "healthy" else "degraded",
            "trading_mode": settings.trading_mode,
            "dependencies": {
                "postgres": {"required": True, "status": snapshot.postgres},
                "redis": {
                    "required": False,
                    "authoritative": False,
                    "status": snapshot.redis,
                },
            },
        }
        envelope["meta"] = {"resource_version": None, "next_cursor": None}
        return JSONResponse(redact(envelope))

    @app.get("/api/v1/markets/{symbol}/status")
    def get_market_status(symbol: str) -> JSONResponse:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            return _market_error("MARKET_NOT_FOUND", "market symbol is not available", 404)
        try:
            snapshot = market_status.get(symbol)
        except MarketProjectionUnavailable:
            return _market_error(
                "MARKET_PROJECTION_UNAVAILABLE",
                "market status projection is unavailable",
                503,
            )
        if snapshot is None:
            return _market_error("MARKET_NOT_FOUND", "market symbol is not available", 404)

        envelope = _base_envelope()
        envelope["data"] = {
            "symbol": snapshot.symbol,
            "price": snapshot.price,
            "event_time": _iso(snapshot.event_time),
            "received_at": _iso(snapshot.received_at),
            "quality": snapshot.quality,
            "quality_reasons": list(snapshot.quality_reasons),
            "watermark": {
                "session_id": snapshot.session_id,
                "stream": snapshot.stream,
                "last_sequence": snapshot.last_sequence,
                "observed_at": _iso(snapshot.watermark_observed_at),
            },
        }
        envelope["meta"] = {"resource_version": None, "next_cursor": None}
        return JSONResponse(redact(envelope))

    return app
