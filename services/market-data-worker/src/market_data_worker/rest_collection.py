"""REST collection that preserves exact response and item provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from .capabilities import PublicRestRequest, RestCapability
from .failures import RateLimitGuard
from .normalization import make_raw_event, normalize
from .pipeline import MarketStore
from .transport import PublicRestTransport
from .types import QualityEvent, QualityStatus


@dataclass(frozen=True, slots=True)
class RestCollectionResult:
    raw_count: int
    normalized_count: int
    status_code: int


class PublicRestCollector:
    def __init__(
        self,
        transport: PublicRestTransport,
        store: MarketStore,
        rate_limit: RateLimitGuard | None = None,
        observed_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._transport = transport
        self._store = store
        self._rate_limit = rate_limit or RateLimitGuard()
        self._observed_clock = observed_clock

    def collect(
        self, request: PublicRestRequest, session_id: str, received_at: datetime
    ) -> RestCollectionResult:
        if not self._rate_limit.may_call(received_at):
            raise RuntimeError("public REST call is blocked by Retry-After")
        response = self._transport.fetch(request)
        # A live request can spend several seconds in DNS/TLS/network I/O.  Its
        # durable received_at must describe receipt, not request start, or the
        # five-second Risk freshness gate could expire before persistence ends.
        observed_at = self._observed_clock() if self._observed_clock is not None else received_at
        if observed_at.tzinfo is None:
            raise ValueError("public REST observed clock must return an aware datetime")
        observed_at = observed_at.astimezone(UTC)
        self._rate_limit.observe(response.status_code, response.headers, observed_at)
        response_raw = make_raw_event(
            session_id,
            f"rest:{request.capability.value}",
            response.raw_bytes,
            observed_at,
            record_kind="rest_response",
            symbol_hint=request.symbol.value if request.symbol is not None else None,
        )
        if not self._store.append_raw(response_raw):
            return RestCollectionResult(0, 0, response.status_code)
        raw_count = 1
        if response.status_code != 200:
            return RestCollectionResult(raw_count, 0, response.status_code)
        if response.payload is None:
            self._store.append_quality(
                QualityEvent(
                    QualityStatus.INVALID,
                    "schema_invalid",
                    response_raw.stream,
                    received_at,
                    response_raw.raw_event_id,
                )
            )
            return RestCollectionResult(raw_count, 0, response.status_code)

        items = response.payload if isinstance(response.payload, list) else [response.payload]
        normalized_count = 0
        for index, item in enumerate(items):
            if not isinstance(item, (dict, list)):
                continue
            raw_item = make_raw_event(
                session_id,
                f"rest-item:{request.capability.value}:{index}",
                item,
                observed_at,
                record_kind="rest_item",
                parent_raw_event_id=response_raw.raw_event_id,
                symbol_hint=request.symbol.value if request.symbol is not None else None,
            )
            if not self._store.append_raw(raw_item):
                continue
            raw_count += 1
            if self._is_incomplete_kline(request, item, observed_at):
                self._store.append_quality(
                    QualityEvent(
                        QualityStatus.INVALID,
                        "kline_incomplete",
                        raw_item.stream,
                        observed_at,
                        raw_item.raw_event_id,
                    )
                )
                continue
            translated = self._translate(request, item, observed_at)
            if translated is None:
                continue
            stream, payload = translated
            derived_raw = make_raw_event(
                session_id,
                stream,
                payload,
                observed_at,
                record_kind="rest_item",
                parent_raw_event_id=raw_item.raw_event_id,
            )
            if not self._store.append_raw(derived_raw):
                continue
            raw_count += 1
            self._store.append_normalized(normalize(derived_raw))
            normalized_count += 1
        return RestCollectionResult(raw_count, normalized_count, response.status_code)

    @staticmethod
    def _is_incomplete_kline(
        request: PublicRestRequest,
        item: dict[str, Any] | list[Any],
        received_at: datetime,
    ) -> bool:
        if request.capability is not RestCapability.KLINES:
            return False
        if not isinstance(item, list) or len(item) < 7:
            return True
        close_milliseconds = item[6]
        if isinstance(close_milliseconds, bool) or not isinstance(close_milliseconds, int):
            return True
        close_time = datetime.fromtimestamp(close_milliseconds / 1000, tz=UTC)
        return close_time > received_at.astimezone(UTC)

    def _translate(
        self,
        request: PublicRestRequest,
        item: dict[str, Any] | list[Any],
        received_at: datetime,
    ) -> tuple[str, dict[str, object]] | None:
        if request.symbol is None:
            return None
        symbol = request.symbol.value
        stream_symbol = request.symbol.stream_value
        if request.capability is RestCapability.TRADES and isinstance(item, dict):
            return f"{stream_symbol}@trade", {
                "e": "trade",
                "E": item.get("time"),
                "s": symbol,
                "t": item.get("id"),
                "p": item.get("price"),
                "q": item.get("qty"),
                "T": item.get("time"),
                "m": item.get("isBuyerMaker"),
                "M": item.get("isBestMatch"),
            }
        if (
            request.capability is RestCapability.KLINES
            and isinstance(item, list)
            and len(item) >= 11
        ):
            interval = request.interval.value if request.interval is not None else ""
            return f"{stream_symbol}@kline_{interval}", {
                "e": "kline",
                "E": item[6],
                "s": symbol,
                "k": {
                    "t": item[0],
                    "T": item[6],
                    "s": symbol,
                    "i": interval,
                    "f": 0,
                    "L": item[0],
                    "o": item[1],
                    "c": item[4],
                    "h": item[2],
                    "l": item[3],
                    "v": item[5],
                    "n": item[8],
                    "x": True,
                    "q": item[7],
                    "V": item[9],
                    "Q": item[10],
                    "B": "0",
                },
            }
        if request.capability is RestCapability.BOOK_TICKER and isinstance(item, dict):
            sequence = int(received_at.timestamp() * 1000)
            return f"{stream_symbol}@bookTicker", {
                "u": sequence,
                "s": symbol,
                "b": item.get("bidPrice"),
                "B": item.get("bidQty"),
                "a": item.get("askPrice"),
                "A": item.get("askQty"),
            }
        return None
