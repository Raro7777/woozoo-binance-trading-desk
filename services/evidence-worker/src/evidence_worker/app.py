"""Internal-only HTTP command boundary for Phase 3 Evidence materialization."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Awaitable, Callable, Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from .persistence import EvidenceCommandResult
from .runner import materialize_evidence_command
from .settings import EvidenceSettings


CommandHandler = Callable[..., EvidenceCommandResult]


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an explicit UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None or not value.endswith("Z"):
        raise ValueError(f"{field} must be an explicit UTC timestamp")
    return parsed.astimezone(UTC)


def create_evidence_app(
    environment: Mapping[str, str] | None = None,
    command_handler: CommandHandler = materialize_evidence_command,
) -> FastAPI:
    settings = EvidenceSettings.from_environment(environment or os.environ)
    command_environment = {
        "TRADING_MODE": "paper",
        "EVIDENCE_DATABASE_URL": settings.database_url,
    }
    app = FastAPI(
        title="Woozoo Evidence Worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def authenticate_internal_principal(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.scope.pop("woozoo.authenticated_service_principal", None)
        extensions = request.scope.get("extensions")
        tls = extensions.get("tls") if isinstance(extensions, dict) else None
        certificate_name = tls.get("client_cert_name") if isinstance(tls, dict) else None
        certificate_error = tls.get("client_cert_error") if isinstance(tls, dict) else None
        if certificate_name == "CN=internal-evidence-scheduler" and certificate_error is None:
            request.scope["woozoo.authenticated_service_principal"] = "internal-evidence-scheduler"
        return await call_next(request)

    @app.post("/api/v1/commands/evidence-snapshots")
    async def create_snapshot(request: Request) -> JSONResponse:
        request_id = str(uuid4())
        served_at = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
        key = request.headers.get("Idempotency-Key", "")
        principal = request.scope.get("woozoo.authenticated_service_principal")
        if principal != "internal-evidence-scheduler":
            return JSONResponse(
                {
                    "api_version": "v1",
                    "request_id": request_id,
                    "correlation_id": request_id,
                    "served_at": served_at,
                    "error": {"code": "CALLER_UNAUTHORIZED"},
                    "meta": {"resource_version": None, "next_cursor": None},
                },
                status_code=403,
            )
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {
                "symbol",
                "as_of",
                "knowledge_cutoff",
            }:
                raise ValueError("command body does not match the closed schema")
            symbol = body["symbol"]
            if symbol not in {"BTCUSDT", "ETHUSDT"}:
                raise ValueError("symbol is outside the Phase 3 allowlist")
            as_of = _utc(body["as_of"], "as_of")
            cutoff = _utc(body["knowledge_cutoff"], "knowledge_cutoff")
            result = command_handler(
                command_environment,
                idempotency_key=key,
                service_principal=principal,
                symbol=symbol,
                as_of=as_of,
                knowledge_cutoff=cutoff,
                created_at=datetime.now(tz=UTC),
            )
        except ValueError as error:
            code = (
                "IDEMPOTENCY_CONFLICT" if str(error) == "IDEMPOTENCY_CONFLICT" else "SCHEMA_INVALID"
            )
            status = 409 if code == "IDEMPOTENCY_CONFLICT" else 400
            return JSONResponse(
                {
                    "api_version": "v1",
                    "request_id": request_id,
                    "correlation_id": request_id,
                    "served_at": served_at,
                    "error": {"code": code},
                    "meta": {"resource_version": None, "next_cursor": None},
                },
                status_code=status,
            )
        return JSONResponse(
            {
                "api_version": "v1",
                "request_id": request_id,
                "correlation_id": request_id,
                "served_at": served_at,
                "data": {
                    "evidence_id": result.evidence_id,
                    "evidence_digest": result.evidence_digest,
                    "created": result.created,
                },
                "meta": {"resource_version": None, "next_cursor": None},
            },
            status_code=201 if result.created else 200,
        )

    return app
