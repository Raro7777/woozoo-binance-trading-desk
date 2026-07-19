from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from control_api.app import create_app
from control_api.dependencies import DependencySnapshot, StaticHealthProbe
from control_api.market_projection import MarketStatusSnapshot, StaticMarketStatusProjection


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


def test_market_status_is_a_closed_injectable_read_projection() -> None:
    observed = datetime(2026, 7, 19, tzinfo=UTC)
    projection = StaticMarketStatusProjection(
        {
            "BTCUSDT": MarketStatusSnapshot(
                symbol="BTCUSDT",
                price="60000.10000000",
                event_time=observed,
                received_at=observed,
                quality="healthy",
                quality_reasons=(),
                session_id="018f7000-0000-7000-8000-000000000001",
                stream="btcusdt@trade",
                last_sequence=100,
                watermark_observed_at=observed,
            )
        }
    )
    app = create_app(
        ENVIRONMENT,
        StaticHealthProbe(DependencySnapshot("healthy", "healthy")),
        market_projection=projection,
    )

    response = request(app, "/api/v1/markets/BTCUSDT/status")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "symbol": "BTCUSDT",
        "price": "60000.10000000",
        "event_time": "2026-07-19T00:00:00Z",
        "received_at": "2026-07-19T00:00:00Z",
        "quality": "healthy",
        "quality_reasons": [],
        "watermark": {
            "session_id": "018f7000-0000-7000-8000-000000000001",
            "stream": "btcusdt@trade",
            "last_sequence": 100,
            "observed_at": "2026-07-19T00:00:00Z",
        },
    }


def test_market_status_rejects_non_allowlisted_symbols_without_projection_io() -> None:
    projection = StaticMarketStatusProjection({})
    app = create_app(
        ENVIRONMENT,
        StaticHealthProbe(DependencySnapshot("healthy", "healthy")),
        market_projection=projection,
    )

    response = request(app, "/api/v1/markets/BNBUSDT/status")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MARKET_NOT_FOUND"
    assert projection.requests == []
