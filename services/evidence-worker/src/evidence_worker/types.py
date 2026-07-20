"""Immutable values for candle features and point-in-time Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re


ALLOWED_INTERVALS = frozenset({"1m", "5m", "1h", "4h"})
HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Candle:
    normalized_event_id: str
    raw_event_id: str
    raw_payload_hash: str
    symbol: str
    interval: str
    event_time: datetime
    received_at: datetime
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    base_volume: Decimal
    closed: bool

    def __post_init__(self) -> None:
        if HASH_PATTERN.fullmatch(self.normalized_event_id) is None:
            raise ValueError("normalized_event_id must be lowercase SHA-256")
        if HASH_PATTERN.fullmatch(self.raw_event_id) is None:
            raise ValueError("raw_event_id must be lowercase SHA-256")
        if HASH_PATTERN.fullmatch(self.raw_payload_hash) is None:
            raise ValueError("raw_payload_hash must be lowercase SHA-256")
        if self.symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("candle symbol is outside the Phase 3 allowlist")
        if self.interval not in ALLOWED_INTERVALS:
            raise ValueError("candle interval is outside the Phase 3 allowlist")
        for field in ("event_time", "received_at", "open_time", "close_time"):
            _aware(getattr(self, field), field)
        for field in ("open", "high", "low", "close"):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a positive finite Decimal")
        if (
            not isinstance(self.base_volume, Decimal)
            or not self.base_volume.is_finite()
            or self.base_volume < 0
        ):
            raise ValueError("base_volume must be a non-negative finite Decimal")
        if self.open_time >= self.close_time:
            raise ValueError("candle close_time must follow open_time")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC bounds are invalid")


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    feature_id: str
    name: str
    definition_version: str
    symbol: str
    interval: str
    feature_time: datetime
    value: Decimal
    value_text: str
    input_digest: str
    input_normalized_event_ids: tuple[str, ...]
    input_raw_event_ids: tuple[str, ...]
    input_raw_payload_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    item_type: str
    item_id: str
    raw_event_id: str
    raw_payload_hash: str


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    evidence_id: str
    evidence_digest: str
    schema_version: str
    recipe_version: str
    symbol: str
    as_of: datetime
    knowledge_cutoff: datetime
    candles: tuple[Candle, ...]
    features: tuple[FeatureObservation, ...]
    input_digest: str
    quality: str
    quality_reasons: tuple[str, ...]
    collector_session_id: str
    watermark_digest: str
    items: tuple[EvidenceItem, ...]
