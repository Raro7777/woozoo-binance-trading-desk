"""Closed URI builders for keyless public market-data capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode


REST_ORIGIN = "https://data-api.binance.vision"
STREAM_ORIGIN = "wss://data-stream.binance.vision"
MAX_STREAMS_PER_CONNECTION = 1024
PUBLIC_ALLOWLIST_VERSION = "binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5"
OFFICIAL_SOURCE_SHA256 = {
    "rest-api.md": "49ea6809243fc7fb426e07f2fe662097736c7bb405bd2da5eef637d715427999",
    "web-socket-streams.md": "32bf73a0bed3b75e3ca981fdbaf48c53544bbdfb5944ef8ed1c4d7af9aceba0a",
    "CHANGELOG.md": "fe345417d817bb7f64f087d87f11204a7311a9e97c13af5a5ed2a8bef26ba172",
}


class Symbol(str, Enum):
    BTCUSDT = "BTCUSDT"
    ETHUSDT = "ETHUSDT"

    @property
    def stream_value(self) -> str:
        return self.value.lower()


class KlineInterval(str, Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"


class RestCapability(str, Enum):
    PING = "/api/v3/ping"
    TIME = "/api/v3/time"
    EXCHANGE_INFO = "/api/v3/exchangeInfo"
    TRADES = "/api/v3/trades"
    KLINES = "/api/v3/klines"
    BOOK_TICKER = "/api/v3/ticker/bookTicker"


class StreamKind(str, Enum):
    TRADE = "trade"
    BOOK_TICKER = "bookTicker"
    KLINE = "kline"


@dataclass(frozen=True, slots=True)
class PublicRestRequest:
    capability: RestCapability
    symbol: Symbol | None = None
    interval: KlineInterval | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability, RestCapability):
            raise TypeError("capability must be a RestCapability")
        if self.symbol is not None and not isinstance(self.symbol, Symbol):
            raise TypeError("symbol must be a Symbol")
        if self.interval is not None and not isinstance(self.interval, KlineInterval):
            raise TypeError("interval must be a KlineInterval")

        no_symbol = {RestCapability.PING, RestCapability.TIME}
        symbol_required = {
            RestCapability.EXCHANGE_INFO,
            RestCapability.TRADES,
            RestCapability.KLINES,
            RestCapability.BOOK_TICKER,
        }
        if self.capability in no_symbol and self.symbol is not None:
            raise ValueError("this capability does not accept a symbol")
        if self.capability in symbol_required and self.symbol is None:
            raise ValueError("this capability requires one allowlisted symbol")
        if self.capability is RestCapability.KLINES and self.interval is None:
            raise ValueError("kline requests require an allowlisted interval")
        if self.capability is not RestCapability.KLINES and self.interval is not None:
            raise ValueError("only kline requests accept an interval")
        if self.limit is not None:
            if self.capability not in {RestCapability.TRADES, RestCapability.KLINES}:
                raise ValueError("this capability does not accept a limit")
            if isinstance(self.limit, bool) or not isinstance(self.limit, int):
                raise TypeError("limit must be an integer")
            if not 1 <= self.limit <= 1000:
                raise ValueError("limit must be between 1 and 1000")

    @property
    def uri(self) -> str:
        query: list[tuple[str, str]] = []
        if self.symbol is not None:
            query.append(("symbol", self.symbol.value))
        if self.interval is not None:
            query.append(("interval", self.interval.value))
        if self.limit is not None:
            query.append(("limit", str(self.limit)))
        suffix = f"?{urlencode(query)}" if query else ""
        return f"{REST_ORIGIN}{self.capability.value}{suffix}"


@dataclass(frozen=True, slots=True)
class PublicStream:
    symbol: Symbol
    kind: StreamKind
    interval: KlineInterval | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise TypeError("symbol must be a Symbol")
        if not isinstance(self.kind, StreamKind):
            raise TypeError("kind must be a StreamKind")
        if self.interval is not None and not isinstance(self.interval, KlineInterval):
            raise TypeError("interval must be a KlineInterval")
        if self.kind is StreamKind.KLINE and self.interval is None:
            raise ValueError("kline streams require an allowlisted interval")
        if self.kind is not StreamKind.KLINE and self.interval is not None:
            raise ValueError("only kline streams accept an interval")

    @property
    def name(self) -> str:
        if self.kind is StreamKind.KLINE:
            assert self.interval is not None
            suffix = f"kline_{self.interval.value}"
        else:
            suffix = self.kind.value
        return f"{self.symbol.stream_value}@{suffix}"


def build_combined_stream_uri(streams: tuple[PublicStream, ...]) -> str:
    if not streams:
        raise ValueError("at least one stream is required")
    if len(streams) > MAX_STREAMS_PER_CONNECTION:
        raise ValueError("stream count exceeds the official connection limit")
    if any(not isinstance(stream, PublicStream) for stream in streams):
        raise TypeError("all streams must be PublicStream values")
    names = tuple(stream.name for stream in streams)
    if len(set(names)) != len(names):
        raise ValueError("duplicate streams are not allowed")
    return f"{STREAM_ORIGIN}/stream?{urlencode({'streams': '/'.join(names)})}"


def fixed_phase_two_streams() -> tuple[PublicStream, ...]:
    streams: list[PublicStream] = []
    for market_symbol in Symbol:
        streams.extend(
            (
                PublicStream(market_symbol, StreamKind.TRADE),
                PublicStream(market_symbol, StreamKind.BOOK_TICKER),
            )
        )
        streams.extend(
            PublicStream(market_symbol, StreamKind.KLINE, interval) for interval in KlineInterval
        )
    return tuple(streams)
