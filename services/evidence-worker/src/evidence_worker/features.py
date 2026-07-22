"""Minimal Decimal-only Phase 3 feature definitions."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext

from .canonical import canonical_digest, decimal_text, iso_utc
from .types import Candle, FeatureObservation


FEATURE_DEFINITION_VERSION = "woozoo.feature.ohlcv-return-sma20-rsi14/v1"
SCALE = Decimal("0.000000000000000001")
FEATURE_WINDOW_SIZE = 21
SMA_PERIOD = 20
RSI_PERIOD = 14
INTERVAL_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}


class FeatureDerivationError(ValueError):
    pass


def _candle_semantics(candle: Candle) -> dict[str, object]:
    return {
        "normalized_event_id": candle.normalized_event_id,
        "raw_event_id": candle.raw_event_id,
        "raw_payload_hash": candle.raw_payload_hash,
        "event_time": iso_utc(candle.event_time),
        "received_at": iso_utc(candle.received_at),
        "open_time": iso_utc(candle.open_time),
        "close_time": iso_utc(candle.close_time),
        "open": decimal_text(candle.open),
        "high": decimal_text(candle.high),
        "low": decimal_text(candle.low),
        "close": decimal_text(candle.close),
        "base_volume": decimal_text(candle.base_volume),
    }


def _observation(name: str, value: Decimal, inputs: tuple[Candle, ...]) -> FeatureObservation:
    latest = inputs[-1]
    input_semantics = {
        "definition_version": FEATURE_DEFINITION_VERSION,
        "feature_name": name,
        "symbol": latest.symbol,
        "interval": latest.interval,
        "feature_time": iso_utc(latest.close_time),
        "inputs": [_candle_semantics(candle) for candle in inputs],
    }
    input_digest = canonical_digest(input_semantics)  # type: ignore[arg-type]
    value_text = decimal_text(value, fixed_scale=18)
    feature_id = canonical_digest(
        {
            "schema_version": "woozoo.feature-observation/v1",
            "input_digest": input_digest,
            "value": value_text,
        }
    )
    return FeatureObservation(
        feature_id=feature_id,
        name=name,
        definition_version=FEATURE_DEFINITION_VERSION,
        symbol=latest.symbol,
        interval=latest.interval,
        feature_time=latest.close_time,
        value=value,
        value_text=value_text,
        input_digest=input_digest,
        input_normalized_event_ids=tuple(candle.normalized_event_id for candle in inputs),
        input_raw_event_ids=tuple(candle.raw_event_id for candle in inputs),
        input_raw_payload_hashes=tuple(candle.raw_payload_hash for candle in inputs),
    )


def derive_interval_features(candles: tuple[Candle, ...]) -> tuple[FeatureObservation, ...]:
    ordered = tuple(sorted(candles, key=lambda item: (item.open_time, item.normalized_event_id)))
    if len(ordered) < FEATURE_WINDOW_SIZE:
        raise FeatureDerivationError("feature derivation requires 21 contiguous closed candles")
    window = ordered[-FEATURE_WINDOW_SIZE:]
    symbol = window[0].symbol
    interval = window[0].interval
    if any(not candle.closed for candle in window):
        raise FeatureDerivationError("feature derivation accepts closed candles only")
    if any(candle.symbol != symbol or candle.interval != interval for candle in window):
        raise FeatureDerivationError("feature window must have one symbol and interval")
    delta = INTERVAL_DELTAS[interval]
    if any(right.open_time != left.open_time + delta for left, right in zip(window, window[1:])):
        raise FeatureDerivationError("feature window must be contiguous")

    try:
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_HALF_EVEN
            close_return = (window[-1].close / window[-2].close - Decimal(1)).quantize(SCALE)
            sma_inputs = window[-SMA_PERIOD:]
            close_sma = (
                sum((item.close for item in sma_inputs), Decimal(0)) / Decimal(SMA_PERIOD)
            ).quantize(SCALE)
            changes = tuple(right.close - left.close for left, right in zip(window, window[1:]))
            gains = tuple(max(change, Decimal(0)) for change in changes)
            losses = tuple(max(-change, Decimal(0)) for change in changes)
            average_gain = sum(gains[:RSI_PERIOD], Decimal(0)) / Decimal(RSI_PERIOD)
            average_loss = sum(losses[:RSI_PERIOD], Decimal(0)) / Decimal(RSI_PERIOD)
            for gain, loss in zip(gains[RSI_PERIOD:], losses[RSI_PERIOD:]):
                average_gain = (average_gain * Decimal(RSI_PERIOD - 1) + gain) / Decimal(RSI_PERIOD)
                average_loss = (average_loss * Decimal(RSI_PERIOD - 1) + loss) / Decimal(RSI_PERIOD)
            if average_loss == 0:
                rsi = Decimal(100) if average_gain > 0 else Decimal(50)
            else:
                relative_strength = average_gain / average_loss
                rsi = Decimal(100) - Decimal(100) / (Decimal(1) + relative_strength)
            close_rsi = rsi.quantize(SCALE)
    except (InvalidOperation, ZeroDivisionError) as error:
        raise FeatureDerivationError("feature Decimal calculation failed") from error

    return (
        _observation("close_return_1", close_return, window[-2:]),
        _observation("close_sma_20", close_sma, sma_inputs),
        _observation("close_rsi_14", close_rsi, window),
    )
