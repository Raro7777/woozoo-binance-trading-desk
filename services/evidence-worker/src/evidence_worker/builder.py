"""Pure point-in-time Evidence selection and immutable digest construction."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from uuid import UUID

from .canonical import canonical_digest, decimal_text, iso_utc
from .features import (
    FEATURE_DEFINITION_VERSION,
    FEATURE_WINDOW_SIZE,
    FeatureDerivationError,
    derive_interval_features,
)
from .types import (
    ALLOWED_INTERVALS,
    HASH_PATTERN,
    Candle,
    EvidenceItem,
    EvidenceSnapshot,
    FeatureObservation,
)


EVIDENCE_SCHEMA_VERSION = "woozoo.evidence-snapshot/v1"
EVIDENCE_RECIPE_VERSION = "woozoo.evidence.closed-candles-approved-features/v1"
SOURCE_REVISION = "binance-spot-api-docs@29c227d84058dd2be3fe3b42ab368d1d1ce910e5"
NORMALIZED_SOURCE_SCHEMA_VERSION = "woozoo.market-event/v1"
RAW_SOURCE_SCHEMA_VERSION = "woozoo.raw-market-event/v1"
INTERVAL_DURATION = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}


class EvidenceBuildError(ValueError):
    pass


def _candle_projection(candle: Candle) -> dict[str, object]:
    return {
        "normalized_event_id": candle.normalized_event_id,
        "raw_event_id": candle.raw_event_id,
        "raw_payload_hash": candle.raw_payload_hash,
        "symbol": candle.symbol,
        "interval": candle.interval,
        "event_time": iso_utc(candle.event_time),
        "received_at": iso_utc(candle.received_at),
        "open_time": iso_utc(candle.open_time),
        "close_time": iso_utc(candle.close_time),
        "open": decimal_text(candle.open),
        "high": decimal_text(candle.high),
        "low": decimal_text(candle.low),
        "close": decimal_text(candle.close),
        "base_volume": decimal_text(candle.base_volume),
        "closed": candle.closed,
    }


def _feature_projection(feature: FeatureObservation) -> dict[str, object]:
    return {
        "feature_id": feature.feature_id,
        "name": feature.name,
        "definition_version": feature.definition_version,
        "symbol": feature.symbol,
        "interval": feature.interval,
        "feature_time": iso_utc(feature.feature_time),
        "value": feature.value_text,
        "input_digest": feature.input_digest,
        "input_normalized_event_ids": list(feature.input_normalized_event_ids),
        "input_raw_event_ids": list(feature.input_raw_event_ids),
        "input_raw_payload_hashes": list(feature.input_raw_payload_hashes),
    }


def build_evidence_snapshot(
    candles: tuple[Candle, ...],
    *,
    symbol: str,
    as_of: datetime,
    knowledge_cutoff: datetime,
    quality: str,
    quality_reasons: tuple[str, ...],
    collector_session_id: str,
    watermark_digest: str,
    required_intervals: tuple[str, ...] = ("1m", "5m", "1h", "4h"),
) -> EvidenceSnapshot:
    try:
        iso_utc(as_of)
        iso_utc(knowledge_cutoff)
    except ValueError as error:
        raise EvidenceBuildError(str(error)) from error
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise EvidenceBuildError("symbol is outside the Phase 3 allowlist")
    if quality != "healthy":
        raise EvidenceBuildError("Evidence creation requires healthy quality")
    if quality_reasons:
        raise EvidenceBuildError("healthy Evidence cannot have quality reasons")
    if not collector_session_id:
        raise EvidenceBuildError("collector_session_id must be non-empty")
    try:
        UUID(collector_session_id)
    except ValueError as error:
        raise EvidenceBuildError("collector_session_id must be a UUID") from error
    if HASH_PATTERN.fullmatch(watermark_digest) is None:
        raise EvidenceBuildError("watermark_digest must be lowercase SHA-256")
    if not required_intervals or len(set(required_intervals)) != len(required_intervals):
        raise EvidenceBuildError("required intervals must be non-empty and unique")
    if any(interval not in ALLOWED_INTERVALS for interval in required_intervals):
        raise EvidenceBuildError("required interval is outside the Phase 3 allowlist")

    grouped: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        if (
            candle.symbol == symbol
            and candle.interval in required_intervals
            and candle.closed
            and candle.event_time <= as_of
            and candle.close_time <= as_of
            and candle.received_at <= knowledge_cutoff
        ):
            grouped[candle.interval].append(candle)

    selected: list[Candle] = []
    features: list[FeatureObservation] = []
    for interval in required_intervals:
        latest_by_open_time: dict[datetime, Candle] = {}
        for candidate in grouped.get(interval, []):
            existing = latest_by_open_time.get(candidate.open_time)
            if existing is None or (
                candidate.received_at,
                candidate.normalized_event_id,
            ) > (existing.received_at, existing.normalized_event_id):
                latest_by_open_time[candidate.open_time] = candidate
        candidates = sorted(
            latest_by_open_time.values(),
            key=lambda item: (item.open_time, item.normalized_event_id),
        )
        if len(candidates) < FEATURE_WINDOW_SIZE:
            raise EvidenceBuildError(
                f"{interval} requires {FEATURE_WINDOW_SIZE} eligible closed candles"
            )
        window = tuple(candidates[-FEATURE_WINDOW_SIZE:])
        effective_horizon = min(as_of, knowledge_cutoff)
        if effective_horizon - window[-1].close_time >= INTERVAL_DURATION[interval]:
            raise EvidenceBuildError(
                f"{interval} terminal candle is stale at the requested cutoffs"
            )
        try:
            derived = derive_interval_features(window)
        except FeatureDerivationError as error:
            raise EvidenceBuildError(f"{interval} feature window is invalid: {error}") from error
        selected.extend(window)
        features.extend(derived)

    ordered_candles = tuple(
        sorted(selected, key=lambda item: (item.interval, item.open_time, item.normalized_event_id))
    )
    ordered_features = tuple(
        sorted(features, key=lambda item: (item.interval, item.name, item.feature_id))
    )
    items: list[EvidenceItem] = [
        EvidenceItem(
            item_type="normalized_market_event",
            item_id=candle.normalized_event_id,
            raw_event_id=candle.raw_event_id,
            raw_payload_hash=candle.raw_payload_hash,
        )
        for candle in ordered_candles
    ]
    for feature in ordered_features:
        items.extend(
            EvidenceItem(
                item_type="feature_observation",
                item_id=feature.feature_id,
                raw_event_id=raw_event_id,
                raw_payload_hash=raw_payload_hash,
            )
            for raw_event_id, raw_payload_hash in zip(
                feature.input_raw_event_ids, feature.input_raw_payload_hashes, strict=True
            )
        )
    ordered_items = tuple(
        sorted(items, key=lambda item: (item.item_type, item.item_id, item.raw_event_id))
    )
    input_semantic = {
        "source_revision": SOURCE_REVISION,
        "normalized_source_schema_version": NORMALIZED_SOURCE_SCHEMA_VERSION,
        "raw_source_schema_version": RAW_SOURCE_SCHEMA_VERSION,
        "feature_definition_version": FEATURE_DEFINITION_VERSION,
        "candles": [_candle_projection(candle) for candle in ordered_candles],
        "features": [_feature_projection(feature) for feature in ordered_features],
        "items": [
            {
                "item_type": item.item_type,
                "item_id": item.item_id,
                "raw_event_id": item.raw_event_id,
                "raw_payload_hash": item.raw_payload_hash,
            }
            for item in ordered_items
        ],
    }
    input_digest = canonical_digest(input_semantic)  # type: ignore[arg-type]
    ordered_reasons = tuple(sorted(set(quality_reasons)))
    semantic = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "recipe_version": EVIDENCE_RECIPE_VERSION,
        "source_revision": SOURCE_REVISION,
        "feature_definition_version": FEATURE_DEFINITION_VERSION,
        "symbol": symbol,
        "as_of": iso_utc(as_of),
        "knowledge_cutoff": iso_utc(knowledge_cutoff),
        "input_digest": input_digest,
        "quality": quality,
        "quality_reasons": list(ordered_reasons),
        "collector_session_id": collector_session_id,
        "watermark_digest": watermark_digest,
    }
    digest = canonical_digest(semantic)  # type: ignore[arg-type]
    return EvidenceSnapshot(
        evidence_id=digest,
        evidence_digest=digest,
        schema_version=EVIDENCE_SCHEMA_VERSION,
        recipe_version=EVIDENCE_RECIPE_VERSION,
        symbol=symbol,
        as_of=as_of,
        knowledge_cutoff=knowledge_cutoff,
        candles=ordered_candles,
        features=ordered_features,
        input_digest=input_digest,
        quality=quality,
        quality_reasons=ordered_reasons,
        collector_session_id=collector_session_id,
        watermark_digest=watermark_digest,
        items=ordered_items,
    )
