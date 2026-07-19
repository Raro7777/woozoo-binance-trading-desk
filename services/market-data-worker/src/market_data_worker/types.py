"""Typed immutable records shared by collection and replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class QualityStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    INVALID = "invalid"
    RECONNECTING = "reconnecting"


@dataclass(frozen=True, slots=True)
class RawMarketEvent:
    raw_event_id: str
    collector_session_id: str
    source: str
    stream: str
    symbol: str | None
    source_event_time: datetime | None
    received_at: datetime
    ingested_at: datetime
    sequence: int | None
    source_dedupe_key: str
    payload_bytes: bytes
    payload_hash: str
    schema_version: str = "woozoo.raw-market-event/v1"
    record_kind: str = "stream_message"
    parent_raw_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class StreamWatermark:
    session_id: str
    stream: str
    last_sequence: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class NormalizedMarketEvent:
    event_id: str
    event_type: str
    schema_version: str
    source: str
    symbol: str
    event_time: datetime
    received_at: datetime
    sequence: int
    raw_event_id: str
    raw_payload_hash: str
    correlation_id: str
    quality_status: QualityStatus
    quality_reasons: tuple[str, ...]
    stream_watermark: StreamWatermark
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class QualityEvent:
    status: QualityStatus
    reason: str
    scope: str
    observed_at: datetime
    raw_event_id: str | None = None


@dataclass(slots=True)
class CollectorState:
    status: QualityStatus = QualityStatus.DEGRADED
    reasons: list[str] = field(default_factory=lambda: ["awaiting_first_valid_event"])
    watermarks: dict[str, StreamWatermark] = field(default_factory=dict)

    def transition(self, status: QualityStatus, reason: str | None = None) -> None:
        self.status = status
        self.reasons = [] if reason is None else [reason]


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: bool
    reason: str
    raw_event: RawMarketEvent | None
    normalized: NormalizedMarketEvent | None
