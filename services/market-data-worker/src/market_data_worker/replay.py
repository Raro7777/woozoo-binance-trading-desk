"""Deterministic recorded-event replay using the live normalization path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from .pipeline import CollectorPipeline, InMemoryMarketStore
from .types import JsonValue, NormalizedMarketEvent, QualityStatus


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    session_id: str
    stream: str
    received_at: datetime
    payload: dict[str, JsonValue]

    def with_payload(self, payload: dict[str, JsonValue]) -> "RecordedEvent":
        return replace(self, payload=payload)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    digest: str
    normalized_count: int
    symbols: tuple[str, ...]
    quality: QualityStatus
    quality_reasons: tuple[str, ...]


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("recorded received_at must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("recorded received_at must be timezone-aware")
    return parsed.astimezone(UTC)


def load_recorded_events(path: Path) -> tuple[RecordedEvent, ...]:
    events: list[RecordedEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            raise ValueError("recorded event must be an object with a payload object")
        session_id = record.get("session_id")
        stream = record.get("stream")
        if not isinstance(session_id, str) or not isinstance(stream, str):
            raise ValueError("recorded event identity is malformed")
        events.append(
            RecordedEvent(
                session_id=session_id,
                stream=stream,
                received_at=parse_timestamp(record.get("received_at")),
                payload=record["payload"],
            )
        )
    return tuple(events)


def event_projection(event: NormalizedMarketEvent) -> dict[str, JsonValue]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "source": event.source,
        "symbol": event.symbol,
        "event_time": event.event_time.isoformat(),
        "received_at": event.received_at.isoformat(),
        "sequence": event.sequence,
        "raw_event_id": event.raw_event_id,
        "raw_payload_hash": event.raw_payload_hash,
        "payload": event.payload,
    }


def replay_recorded_events(events: tuple[RecordedEvent, ...]) -> ReplayResult:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)
    for event in events:
        pipeline.ingest(event.session_id, event.stream, event.payload, event.received_at)
    projected = [event_projection(event) for event in store.normalized_events]
    quality = [
        {
            "status": event.status.value,
            "reason": event.reason,
            "scope": event.scope,
            "observed_at": event.observed_at.isoformat(),
            "raw_event_id": event.raw_event_id,
        }
        for event in store.quality_events
    ]
    watermarks = {
        stream: {
            "session_id": watermark.session_id,
            "last_sequence": watermark.last_sequence,
            "observed_at": watermark.observed_at.isoformat(),
        }
        for stream, watermark in sorted(pipeline.state.watermarks.items())
    }
    semantic = {
        "normalized": projected,
        "quality_transitions": quality,
        "watermarks": watermarks,
        "final_quality": pipeline.state.status.value,
        "final_reasons": pipeline.state.reasons,
    }
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    return ReplayResult(
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        normalized_count=len(store.normalized_events),
        symbols=tuple(sorted({event.symbol for event in store.normalized_events})),
        quality=pipeline.state.status,
        quality_reasons=tuple(pipeline.state.reasons),
    )
