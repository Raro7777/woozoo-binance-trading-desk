"""Strict normalization for approved public Spot stream payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, cast

from .capabilities import KlineInterval, StreamKind, Symbol
from .types import JsonValue, NormalizedMarketEvent, QualityStatus, RawMarketEvent, StreamWatermark


SOURCE = "binance_spot_public"
MARKET_SCHEMA_VERSION = "woozoo.market-event/v1"


class SchemaInvalid(ValueError):
    pass


def canonical_payload_bytes(payload: object) -> bytes:
    def reject_floats(value: object) -> None:
        if isinstance(value, float):
            raise SchemaInvalid("floating point payload values are not accepted")
        if isinstance(value, list):
            for item in value:
                reject_floats(item)
        elif isinstance(value, dict):
            for item in value.values():
                reject_floats(item)

    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    reject_floats(payload)
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SchemaInvalid("payload is not canonical JSON") from error


def parse_payload(payload_bytes: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaInvalid("payload is not valid UTF-8 JSON") from error
    if not isinstance(parsed, dict):
        raise SchemaInvalid("stream payload must be an object")
    if set(parsed) == {"stream", "data"}:
        data = parsed["data"]
        if not isinstance(parsed["stream"], str) or not isinstance(data, dict):
            raise SchemaInvalid("combined stream envelope is malformed")
        return cast(dict[str, Any], data)
    return cast(dict[str, Any], parsed)


def milliseconds(value: object, field: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaInvalid(f"{field} must be a non-negative millisecond integer")
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaInvalid(f"{field} must be a non-negative integer")
    return value


def decimal_string(value: object, field: str, *, positive: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaInvalid(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise SchemaInvalid(f"{field} must be a decimal string") from error
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise SchemaInvalid(f"{field} is outside the accepted range")
    return value


def boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaInvalid(f"{field} must be a boolean")
    return value


def symbol(value: object) -> Symbol:
    if not isinstance(value, str):
        raise SchemaInvalid("symbol must be a string")
    try:
        return Symbol(value)
    except ValueError as error:
        raise SchemaInvalid("symbol is outside the Phase 2 allowlist") from error


def stream_parts(stream: str) -> tuple[Symbol, StreamKind, KlineInterval | None]:
    if "@" not in stream:
        raise SchemaInvalid("stream name is not an approved symbol stream")
    symbol_text, suffix = stream.split("@", 1)
    try:
        stream_symbol = Symbol(symbol_text.upper())
    except ValueError as error:
        raise SchemaInvalid("stream symbol is outside the allowlist") from error
    if suffix == StreamKind.TRADE.value:
        return stream_symbol, StreamKind.TRADE, None
    if suffix == StreamKind.BOOK_TICKER.value:
        return stream_symbol, StreamKind.BOOK_TICKER, None
    if suffix.startswith("kline_"):
        try:
            return stream_symbol, StreamKind.KLINE, KlineInterval(suffix.removeprefix("kline_"))
        except ValueError as error:
            raise SchemaInvalid("kline interval is outside the allowlist") from error
    raise SchemaInvalid("stream kind is outside the allowlist")


def make_raw_event(
    session_id: str,
    stream: str,
    payload: object,
    received_at: datetime,
    *,
    record_kind: str = "stream_message",
    parent_raw_event_id: str | None = None,
    symbol_hint: str | None = None,
) -> RawMarketEvent:
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    payload_bytes = canonical_payload_bytes(payload)
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    parsed: dict[str, Any] = {}
    try:
        parsed = parse_payload(payload_bytes)
    except SchemaInvalid:
        pass
    raw_symbol = parsed.get("s", symbol_hint)
    if not isinstance(raw_symbol, str):
        kline = parsed.get("k")
        raw_symbol = kline.get("s") if isinstance(kline, dict) else None
    allowed_symbol = raw_symbol if raw_symbol in {item.value for item in Symbol} else None
    source_time = None
    if isinstance(parsed.get("E"), int) and not isinstance(parsed.get("E"), bool):
        source_time = milliseconds(parsed["E"], "E")
    sequence: int | None = None
    prefix = "invalid"
    source_key_override: str | None = None
    if parsed.get("e") == "trade" and isinstance(parsed.get("t"), int):
        sequence = parsed["t"]
        prefix = "trade"
    elif "u" in parsed and isinstance(parsed.get("u"), int):
        sequence = parsed["u"]
        prefix = "book_ticker"
    elif parsed.get("e") == "kline" and isinstance(parsed.get("k"), dict):
        kline_payload = parsed["k"]
        last_trade = kline_payload.get("L")
        if isinstance(last_trade, int):
            sequence = last_trade
        prefix = "kline"
        interval = kline_payload.get("i")
        open_time = kline_payload.get("t")
        closed = kline_payload.get("x")
        source_event = parsed.get("E")
        if (
            isinstance(interval, str)
            and isinstance(open_time, int)
            and not isinstance(open_time, bool)
            and isinstance(closed, bool)
            and isinstance(source_event, int)
            and not isinstance(source_event, bool)
        ):
            source_key_override = (
                f"kline:{allowed_symbol or '-'}:{interval}:{open_time}:"
                f"{source_event}:{int(closed)}:{payload_hash}"
            )
    elif parsed.get("e") == "serverShutdown":
        prefix = "server_shutdown"
    source_key = source_key_override or (
        f"{prefix}:{allowed_symbol or '-'}:{sequence if sequence is not None else payload_hash}"
    )
    identity = "|".join((session_id, stream, received_at.isoformat(), payload_hash))
    return RawMarketEvent(
        raw_event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        collector_session_id=session_id,
        source=SOURCE,
        stream=stream,
        symbol=allowed_symbol,
        source_event_time=source_time,
        received_at=received_at.astimezone(UTC),
        ingested_at=received_at.astimezone(UTC),
        sequence=sequence,
        source_dedupe_key=source_key,
        payload_bytes=payload_bytes,
        payload_hash=payload_hash,
        record_kind=record_kind,
        parent_raw_event_id=parent_raw_event_id,
    )


def normalize(raw: RawMarketEvent) -> NormalizedMarketEvent:
    payload = parse_payload(raw.payload_bytes)
    stream_symbol, stream_kind, interval = stream_parts(raw.stream)
    payload_symbol = symbol(payload.get("s"))
    if payload_symbol is not stream_symbol:
        raise SchemaInvalid("stream and payload symbols do not match")

    event_type: str
    event_time: datetime
    sequence: int
    values: dict[str, JsonValue]
    if stream_kind is StreamKind.TRADE:
        required = {"e", "E", "s", "t", "p", "q", "T", "m", "M"}
        if set(payload) != required or payload.get("e") != "trade":
            raise SchemaInvalid("trade payload fields do not match the contract")
        event_type = "trade"
        event_time = milliseconds(payload["T"], "T")
        sequence = integer(payload["t"], "t")
        values = {
            "kind": "trade",
            "price": decimal_string(payload["p"], "p", positive=True),
            "quantity": decimal_string(payload["q"], "q", positive=True),
            "buyer_maker": boolean(payload["m"], "m"),
        }
        milliseconds(payload["E"], "E")
        boolean(payload["M"], "M")
    elif stream_kind is StreamKind.BOOK_TICKER:
        required = {"u", "s", "b", "B", "a", "A"}
        if set(payload) != required:
            raise SchemaInvalid("book ticker payload fields do not match the contract")
        event_type = "book_ticker"
        event_time = raw.received_at
        sequence = integer(payload["u"], "u")
        values = {
            "kind": "book_ticker",
            "bid_price": decimal_string(payload["b"], "b", positive=True),
            "bid_quantity": decimal_string(payload["B"], "B"),
            "ask_price": decimal_string(payload["a"], "a", positive=True),
            "ask_quantity": decimal_string(payload["A"], "A"),
            "event_time_source": "received_at",
        }
    else:
        assert interval is not None
        required = {"e", "E", "s", "k"}
        if set(payload) != required or payload.get("e") != "kline":
            raise SchemaInvalid("kline envelope fields do not match the contract")
        kline = payload["k"]
        if not isinstance(kline, dict):
            raise SchemaInvalid("kline payload must be an object")
        kline_required = {
            "t",
            "T",
            "s",
            "i",
            "f",
            "L",
            "o",
            "c",
            "h",
            "l",
            "v",
            "n",
            "x",
            "q",
            "V",
            "Q",
            "B",
        }
        if set(kline) != kline_required:
            raise SchemaInvalid("kline payload fields do not match the contract")
        if symbol(kline["s"]) is not stream_symbol or kline["i"] != interval.value:
            raise SchemaInvalid("kline stream metadata does not match its payload")
        event_type = "kline"
        event_time = milliseconds(payload["E"], "E")
        sequence = integer(kline["L"], "L")
        values = {
            "kind": "kline",
            "interval": interval.value,
            "open_time": milliseconds(kline["t"], "t").isoformat().replace("+00:00", "Z"),
            "close_time": milliseconds(kline["T"], "T").isoformat().replace("+00:00", "Z"),
            "open": decimal_string(kline["o"], "o", positive=True),
            "close": decimal_string(kline["c"], "c", positive=True),
            "high": decimal_string(kline["h"], "h", positive=True),
            "low": decimal_string(kline["l"], "l", positive=True),
            "base_volume": decimal_string(kline["v"], "v"),
            "trade_count": integer(kline["n"], "n"),
            "closed": boolean(kline["x"], "x"),
        }
        integer(kline["f"], "f")
        decimal_string(kline["q"], "q")
        decimal_string(kline["V"], "V")
        decimal_string(kline["Q"], "Q")
        if not isinstance(kline["B"], str):
            raise SchemaInvalid("B must remain a source string")

    watermark = StreamWatermark(
        session_id=raw.collector_session_id,
        stream=raw.stream,
        last_sequence=sequence,
        observed_at=raw.received_at,
    )
    event_id = hashlib.sha256(
        f"{MARKET_SCHEMA_VERSION}|{raw.raw_event_id}|{event_type}|{sequence}".encode("utf-8")
    ).hexdigest()
    return NormalizedMarketEvent(
        event_id=event_id,
        event_type=event_type,
        schema_version=MARKET_SCHEMA_VERSION,
        source=SOURCE,
        symbol=stream_symbol.value,
        event_time=event_time,
        received_at=raw.received_at,
        sequence=sequence,
        raw_event_id=raw.raw_event_id,
        raw_payload_hash=raw.payload_hash,
        correlation_id=raw.collector_session_id,
        quality_status=QualityStatus.HEALTHY,
        quality_reasons=(),
        stream_watermark=watermark,
        payload=values,
    )


def is_server_shutdown(raw: RawMarketEvent) -> bool:
    if raw.stream != "!serverShutdown":
        return False
    try:
        parsed = parse_payload(raw.payload_bytes)
    except SchemaInvalid:
        return False
    return (
        parsed.get("e") == "serverShutdown"
        and set(parsed) == {"e", "E"}
        and isinstance(parsed.get("E"), int)
    )
