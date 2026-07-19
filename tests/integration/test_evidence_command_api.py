from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from evidence_worker.app import create_evidence_app
from evidence_worker.persistence import EvidenceCommandResult


def request(
    app: object,
    body: object,
    *,
    key: str = "evid-command-1",
    principal: str | None = "internal-evidence-scheduler",
    certificate_error: str | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async def trusted_ingress(scope: object, receive: object, send_response: object) -> None:
            authenticated_scope = dict(scope)  # type: ignore[arg-type]
            if principal is not None:
                extensions = dict(authenticated_scope.get("extensions", {}))
                extensions["tls"] = {
                    "client_cert_name": f"CN={principal}",
                    "client_cert_error": certificate_error,
                }
                authenticated_scope["extensions"] = extensions
            await app(authenticated_scope, receive, send_response)  # type: ignore[operator]

        transport = httpx.ASGITransport(app=trusted_ingress)
        async with httpx.AsyncClient(transport=transport, base_url="http://evidence") as client:
            return await client.post(
                "/api/v1/commands/evidence-snapshots",
                json=body,
                headers={"Idempotency-Key": key},
            )

    return asyncio.run(send())


def test_evidence_command_requires_both_utc_cutoffs_and_idempotency_key() -> None:
    calls: list[dict[str, object]] = []

    def handler(environment: object, **values: object) -> EvidenceCommandResult:
        calls.append(values)
        timestamp = values["as_of"]
        assert isinstance(timestamp, datetime)
        return EvidenceCommandResult("a" * 64, "a" * 64, True)

    app = create_evidence_app(
        {
            "TRADING_MODE": "paper",
            "EVIDENCE_DATABASE_URL": "postgresql://woozoo_evidence_writer@postgres/woozoo",
        },
        handler,
    )
    response = request(
        app,
        {
            "symbol": "BTCUSDT",
            "as_of": "2026-07-19T00:00:00Z",
            "knowledge_cutoff": "2026-07-18T23:00:00Z",
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["evidence_id"] == "a" * 64
    assert calls[0]["idempotency_key"] == "evid-command-1"
    assert calls[0]["knowledge_cutoff"] == datetime(2026, 7, 18, 23, tzinfo=UTC)
    assert calls[0]["service_principal"] == "internal-evidence-scheduler"

    invalid = request(app, {"symbol": "BTCUSDT", "as_of": "2026-07-19T00:00:00Z"})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "SCHEMA_INVALID"

    unauthorized = request(
        app,
        {
            "symbol": "BTCUSDT",
            "as_of": "2026-07-19T00:00:00Z",
            "knowledge_cutoff": "2026-07-18T23:00:00Z",
        },
        principal=None,
    )
    assert unauthorized.status_code == 403
    assert unauthorized.json()["error"]["code"] == "CALLER_UNAUTHORIZED"
    unknown = request(
        app,
        {
            "symbol": "BTCUSDT",
            "as_of": "2026-07-19T00:00:00Z",
            "knowledge_cutoff": "2026-07-18T23:00:00Z",
        },
        principal="unknown-service",
    )
    assert unknown.status_code == 403
    expired = request(
        app,
        {
            "symbol": "BTCUSDT",
            "as_of": "2026-07-19T00:00:00Z",
            "knowledge_cutoff": "2026-07-18T23:00:00Z",
        },
        certificate_error="certificate expired",
    )
    assert expired.status_code == 403
    assert len(calls) == 1
