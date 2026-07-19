from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from control_api.app import create_app
from control_api.dependencies import DependencySnapshot, StaticHealthProbe
from control_api.evidence_projection import (
    EvidenceCandleSnapshot,
    EvidenceFeatureSnapshot,
    EvidenceItemSnapshot,
    EvidenceSnapshot,
    StaticEvidenceProjection,
)


ENVIRONMENT = {
    "TRADING_MODE": "paper",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
    "REDIS_URL": "redis://127.0.0.1:6380/0",
}


def request(app: object, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(send())


def test_evidence_query_returns_immutable_point_in_time_snapshot() -> None:
    timestamp = datetime(2026, 7, 19, tzinfo=UTC)
    evidence_id = "a" * 64
    projection = StaticEvidenceProjection(
        {
            evidence_id: EvidenceSnapshot(
                evidence_id=evidence_id,
                evidence_digest="b" * 64,
                symbol="BTCUSDT",
                as_of=timestamp,
                knowledge_cutoff=timestamp,
                recipe_version="woozoo.evidence.closed-candles-approved-features/v1",
                input_digest="c" * 64,
                quality="healthy",
                quality_reasons=(),
                collector_session_id="018f7000-0000-7000-8000-000000000001",
                watermark_digest="d" * 64,
                items=(
                    EvidenceItemSnapshot(
                        item_type="feature_observation",
                        item_id="e" * 64,
                        raw_event_id="f" * 64,
                        raw_payload_hash="0" * 64,
                    ),
                ),
                candles=(
                    EvidenceCandleSnapshot(
                        normalized_event_id="1" * 64,
                        raw_event_id="f" * 64,
                        raw_payload_hash="0" * 64,
                        interval="1m",
                        event_time=timestamp,
                        received_at=timestamp,
                        open_time="2026-07-18T23:59:00Z",
                        close_time="2026-07-19T00:00:00Z",
                        open="100",
                        high="101",
                        low="99",
                        close="100",
                        base_volume="10",
                    ),
                ),
                features=(
                    EvidenceFeatureSnapshot(
                        feature_id="e" * 64,
                        name="close_sma_20",
                        definition_version="woozoo.feature.ohlcv-return-sma20-rsi14/v1",
                        interval="1m",
                        feature_time=timestamp,
                        value="100.000000000000000000",
                        input_digest="2" * 64,
                    ),
                ),
            )
        }
    )
    app = create_app(
        ENVIRONMENT,
        StaticHealthProbe(DependencySnapshot("healthy", "healthy")),
        evidence_projection=projection,
    )

    response = request(app, f"/api/v1/evidence/{evidence_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["evidence_id"] == evidence_id
    assert data["evidence_digest"] == "b" * 64
    assert data["as_of"] == "2026-07-19T00:00:00Z"
    assert data["knowledge_cutoff"] == "2026-07-19T00:00:00Z"
    assert data["items"] == [
        {
            "item_type": "feature_observation",
            "item_id": "e" * 64,
            "raw_event_id": "f" * 64,
            "raw_payload_hash": "0" * 64,
        }
    ]
    assert data["candles"][0]["close"] == "100"
    assert data["features"][0]["name"] == "close_sma_20"


def test_evidence_query_is_closed_and_does_not_expose_raw_payload() -> None:
    projection = StaticEvidenceProjection({})
    app = create_app(
        ENVIRONMENT,
        StaticHealthProbe(DependencySnapshot("healthy", "healthy")),
        evidence_projection=projection,
    )

    response = request(app, "/api/v1/evidence/not-a-digest")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"
    assert projection.requests == []
