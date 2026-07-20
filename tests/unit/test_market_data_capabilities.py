from __future__ import annotations

from typing import cast

import pytest

from market_data_worker.capabilities import (
    KlineInterval,
    PublicRestRequest,
    PublicStream,
    RestCapability,
    StreamKind,
    Symbol,
    build_combined_stream_uri,
    fixed_phase_two_streams,
)


def test_public_uris_are_closed_to_keyless_btc_eth_market_data() -> None:
    trades = PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT)
    klines = PublicRestRequest(
        RestCapability.KLINES,
        Symbol.ETHUSDT,
        interval=KlineInterval.ONE_MINUTE,
        limit=100,
    )

    assert trades.uri == "https://data-api.binance.vision/api/v3/trades?symbol=BTCUSDT"
    assert klines.uri == (
        "https://data-api.binance.vision/api/v3/klines?symbol=ETHUSDT&interval=1m&limit=100"
    )
    assert build_combined_stream_uri(
        (
            PublicStream(Symbol.BTCUSDT, StreamKind.TRADE),
            PublicStream(Symbol.ETHUSDT, StreamKind.BOOK_TICKER),
        )
    ) == ("wss://data-stream.binance.vision/stream?streams=btcusdt%40trade%2Fethusdt%40bookTicker")


def test_public_uri_types_reject_unapproved_values_before_network_io() -> None:
    with pytest.raises(TypeError):
        PublicRestRequest(cast(RestCapability, "POST"), Symbol.BTCUSDT)
    with pytest.raises(TypeError):
        PublicRestRequest(RestCapability.TRADES, cast(Symbol, "BNBUSDT"))
    with pytest.raises(ValueError):
        PublicRestRequest(RestCapability.PING, Symbol.BTCUSDT)
    with pytest.raises(ValueError):
        PublicRestRequest(RestCapability.KLINES, Symbol.BTCUSDT)
    with pytest.raises(TypeError):
        PublicStream(Symbol.BTCUSDT, cast(StreamKind, "depth"))


def test_kline_intervals_are_exactly_the_phase_two_allowlist() -> None:
    assert {interval.value for interval in KlineInterval} == {"1m", "5m", "1h", "4h"}
    streams = fixed_phase_two_streams()
    assert len(streams) == 12
    assert len({stream.name for stream in streams}) == 12
