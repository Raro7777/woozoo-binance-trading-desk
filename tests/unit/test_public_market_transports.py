from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from market_data_worker.capabilities import (
    PublicRestRequest,
    PublicStream,
    RestCapability,
    StreamKind,
    Symbol,
)
from market_data_worker.transport import PublicRestTransport, PublicWebSocketTransport
from market_data_worker.transport import default_get


class FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def json(self) -> object:
        return [{"id": 1}]


class OversizedResponse:
    status_code = 200
    headers: dict[str, str] = {}
    content = b"0" * (8 * 1024 * 1024 + 1)

    def json(self) -> object:
        return None


def test_rest_transport_accepts_only_a_typed_get_without_authentication_inputs() -> None:
    calls: list[tuple[str, float]] = []

    def get(uri: str, timeout: float) -> FakeResponse:
        calls.append((uri, timeout))
        return FakeResponse()

    response = PublicRestTransport(get=get).fetch(
        PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT)
    )

    assert response.payload == [{"id": 1}]
    assert calls == [("https://data-api.binance.vision/api/v3/trades?symbol=BTCUSDT", 10.0)]


def test_default_rest_transport_ignores_proxy_environment_and_redirects(monkeypatch: Any) -> None:
    observed: dict[str, object] = {}

    class Client:
        def __init__(self, **options: object) -> None:
            observed["options"] = options

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, uri: str, *, timeout: float) -> FakeResponse:
            observed["request"] = (uri, timeout)
            return FakeResponse()

    monkeypatch.setattr("market_data_worker.transport.httpx.Client", Client)

    default_get("https://data-api.binance.vision/api/v3/ping", 10.0)

    assert observed["options"] == {"trust_env": False, "follow_redirects": False}
    assert observed["request"] == ("https://data-api.binance.vision/api/v3/ping", 10.0)


def test_rest_transport_rejects_responses_over_the_fixed_limit() -> None:
    import pytest

    transport = PublicRestTransport(get=lambda _uri, _timeout: OversizedResponse())
    with pytest.raises(RuntimeError, match="8 MiB"):
        transport.fetch(PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT))


def test_websocket_transport_connects_only_to_the_typed_market_stream_uri() -> None:
    observed: list[str] = []

    class Connection:
        def __aiter__(self) -> AsyncIterator[str]:
            async def messages() -> AsyncIterator[str]:
                yield '{"e":"serverShutdown","E":1784419200000}'

            return messages()

        async def __aenter__(self) -> "Connection":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

    def connect(uri: str) -> Connection:
        observed.append(uri)
        return Connection()

    async def collect() -> list[bytes]:
        transport = PublicWebSocketTransport(connect=connect)
        return [
            message
            async for message in transport.messages(
                (PublicStream(Symbol.ETHUSDT, StreamKind.TRADE),)
            )
        ]

    assert asyncio.run(collect()) == [b'{"e":"serverShutdown","E":1784419200000}']
    assert observed == ["wss://data-stream.binance.vision/stream?streams=ethusdt%40trade"]
