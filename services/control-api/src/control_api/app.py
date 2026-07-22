"""The deliberately small Phase 1 health API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from platform_core.config import PlatformSettings
from platform_core.generated_contracts import API_VERSION, HEALTH_PATH
from platform_core.primitives import new_request_id, utc_now
from platform_core.redaction import redact

from .dependencies import DependencySnapshot, HealthProbe, PlatformHealthProbe
from .command_ports import (
    PostgresAgentAnalysisPort,
    PostgresPaperCommandPort,
    PostgresRiskCommandPort,
    PostgresRiskEvaluationPort,
)
from .evidence_projection import (
    EVIDENCE_ID_PATTERN,
    EvidenceProjection,
    EvidenceProjectionUnavailable,
    PostgresEvidenceProjection,
)
from .market_projection import (
    MarketProjectionUnavailable,
    MarketStatusProjection,
    PostgresMarketStatusProjection,
)
from .security import LocalOperatorSecurity
from .trading_room import PostgresTradingRoom, TradingRoom
from .trading_room_routes import load_local_security, register_trading_room_routes


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


def _evidence_error(code: str, message: str, status_code: int) -> JSONResponse:
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
    evidence_projection: EvidenceProjection | None = None,
    operator_security: LocalOperatorSecurity | None = None,
    trading_room: TradingRoom | None = None,
) -> FastAPI:
    values = os.environ if environment is None else environment
    settings = PlatformSettings.from_mapping(values).require_service_dependencies()
    health_probe = probe or PlatformHealthProbe()
    assert settings.database_url is not None
    market_status = market_projection or PostgresMarketStatusProjection(settings.database_url)
    evidence_reader = evidence_projection or PostgresEvidenceProjection(settings.database_url)
    app = ContractBoundFastAPI(docs_url=None, openapi_url=None, redoc_url=None)

    @app.exception_handler(RequestValidationError)
    async def sanitized_request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request, error
        return JSONResponse(
            {
                "error": {
                    "code": "REQUEST_VALIDATION_FAILED",
                    "message": "request validation failed",
                }
            },
            status_code=422,
        )

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

    @app.get("/api/v1/evidence/{evidence_id}")
    def get_evidence(evidence_id: str) -> JSONResponse:
        if EVIDENCE_ID_PATTERN.fullmatch(evidence_id) is None:
            return _evidence_error("EVIDENCE_NOT_FOUND", "evidence snapshot is not available", 404)
        try:
            snapshot = evidence_reader.get(evidence_id)
        except EvidenceProjectionUnavailable:
            return _evidence_error(
                "EVIDENCE_PROJECTION_UNAVAILABLE",
                "evidence projection is unavailable",
                503,
            )
        if snapshot is None:
            return _evidence_error("EVIDENCE_NOT_FOUND", "evidence snapshot is not available", 404)

        envelope = _base_envelope()
        envelope["data"] = {
            "evidence_id": snapshot.evidence_id,
            "evidence_digest": snapshot.evidence_digest,
            "symbol": snapshot.symbol,
            "as_of": _iso(snapshot.as_of),
            "knowledge_cutoff": _iso(snapshot.knowledge_cutoff),
            "recipe_version": snapshot.recipe_version,
            "input_digest": snapshot.input_digest,
            "quality": snapshot.quality,
            "quality_reasons": list(snapshot.quality_reasons),
            "collector_session_id": snapshot.collector_session_id,
            "watermark_digest": snapshot.watermark_digest,
            "items": [
                {
                    "item_type": item.item_type,
                    "item_id": item.item_id,
                    "raw_event_id": item.raw_event_id,
                    "raw_payload_hash": item.raw_payload_hash,
                }
                for item in snapshot.items
            ],
            "candles": [
                {
                    "normalized_event_id": candle.normalized_event_id,
                    "raw_event_id": candle.raw_event_id,
                    "raw_payload_hash": candle.raw_payload_hash,
                    "interval": candle.interval,
                    "event_time": _iso(candle.event_time),
                    "received_at": _iso(candle.received_at),
                    "open_time": candle.open_time,
                    "close_time": candle.close_time,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "base_volume": candle.base_volume,
                }
                for candle in snapshot.candles
            ],
            "features": [
                {
                    "feature_id": feature.feature_id,
                    "name": feature.name,
                    "definition_version": feature.definition_version,
                    "interval": feature.interval,
                    "feature_time": _iso(feature.feature_time),
                    "value": feature.value,
                    "input_digest": feature.input_digest,
                }
                for feature in snapshot.features
            ],
        }
        envelope["meta"] = {"resource_version": None, "next_cursor": None}
        return JSONResponse(redact(envelope))

    local_security = operator_security or load_local_security(values)
    if local_security is not None:
        room = trading_room
        if room is None:
            control_database_url = values.get("CONTROL_DATABASE_URL", "").strip()
            if not control_database_url:
                raise RuntimeError(
                    "CONTROL_DATABASE_URL is required for the Postgres Trading Room authority"
                )
            risk_database_url = values.get("RISK_DATABASE_URL", "").strip()
            paper_database_url = values.get("PAPER_DATABASE_URL", "").strip()
            agent_database_url = values.get("AGENT_DATABASE_URL", "").strip()
            room = PostgresTradingRoom(
                control_database_url,
                risk_commands=(
                    PostgresRiskCommandPort(risk_database_url) if risk_database_url else None
                ),
                paper_commands=(
                    PostgresPaperCommandPort(paper_database_url) if paper_database_url else None
                ),
                agent_analysis=(
                    PostgresAgentAnalysisPort(agent_database_url) if agent_database_url else None
                ),
                risk_evaluation=(
                    PostgresRiskEvaluationPort(risk_database_url) if risk_database_url else None
                ),
            )
        register_trading_room_routes(
            app,
            local_security,
            room,
        )

    return app
