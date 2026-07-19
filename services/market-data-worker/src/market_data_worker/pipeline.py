"""Raw-first ingestion pipeline with explicit quality transitions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Mapping, Protocol

from .capabilities import fixed_phase_two_streams
from .normalization import SchemaInvalid, is_server_shutdown, make_raw_event, normalize
from .types import (
    CollectorState,
    IngestResult,
    NormalizedMarketEvent,
    QualityEvent,
    QualityStatus,
    RawMarketEvent,
    StreamWatermark,
)


class MarketStore(Protocol):
    def append_raw(self, event: RawMarketEvent) -> bool: ...

    def append_normalized(self, event: NormalizedMarketEvent) -> None: ...

    def append_quality(self, event: QualityEvent) -> None: ...


class RawDedupeConflict(RuntimeError):
    """The same source identity was observed with different immutable bytes."""


class InMemoryMarketStore:
    def __init__(self) -> None:
        self.raw_events: list[RawMarketEvent] = []
        self.normalized_events: list[NormalizedMarketEvent] = []
        self.operations: list[str] = []
        self.quality_events: list[QualityEvent] = []
        self._raw_dedupe: dict[tuple[str, str, str | None, str], str] = {}
        self._normalized_raw_ids: set[str] = set()

    def append_raw(self, event: RawMarketEvent) -> bool:
        key = (event.source, event.stream, event.symbol, event.source_dedupe_key)
        existing_hash = self._raw_dedupe.get(key)
        if existing_hash is not None:
            if existing_hash != event.payload_hash:
                raise RawDedupeConflict("source identity payload hash mismatch")
            return False
        self._raw_dedupe[key] = event.payload_hash
        self.raw_events.append(event)
        self.operations.append("append_raw")
        return True

    def append_normalized(self, event: NormalizedMarketEvent) -> None:
        if event.raw_event_id in self._normalized_raw_ids:
            return
        self._normalized_raw_ids.add(event.raw_event_id)
        self.normalized_events.append(event)
        self.operations.append("append_normalized")

    def has_normalized(self, raw_event_id: str) -> bool:
        return raw_event_id in self._normalized_raw_ids

    def append_quality(self, event: QualityEvent) -> None:
        self.quality_events.append(event)


class CollectorPipeline:
    def __init__(self, store: MarketStore) -> None:
        self.store = store
        self.state = CollectorState()
        self.quality_events: list[QualityEvent] = []
        self._last_sequence: dict[tuple[str, str], int] = {}
        self._current_session: str | None = None
        self._confirmed_sessions: set[str] = set()
        expected = tuple(stream.name for stream in fixed_phase_two_streams())
        self._expected_streams = frozenset(expected)
        self._expected_by_symbol = {
            symbol: frozenset(name for name in expected if name.startswith(f"{symbol.lower()}@"))
            for symbol in ("BTCUSDT", "ETHUSDT")
        }
        self._stream_quality: dict[str, tuple[QualityStatus, str | None]] = {
            stream: (QualityStatus.DEGRADED, "stream_incomplete") for stream in expected
        }
        self._stream_received_at: dict[str, datetime] = {}
        self._closed_kline_open: dict[tuple[str, str], datetime] = {}
        self._refresh_state()

    def confirm_generation(self, session_id: str) -> None:
        self._confirmed_sessions.add(session_id)
        self._current_session = session_id
        self._refresh_state()

    def _refresh_state(self) -> None:
        if not self._stream_quality:
            self.state.transition(QualityStatus.DEGRADED, "awaiting_first_valid_event")
            return
        priority = {
            QualityStatus.HEALTHY: 0,
            QualityStatus.DEGRADED: 1,
            QualityStatus.RECONNECTING: 2,
            QualityStatus.STALE: 3,
            QualityStatus.INVALID: 4,
        }
        worst = max(priority[status] for status, _reason in self._stream_quality.values())
        statuses = [
            (status, reason)
            for status, reason in self._stream_quality.values()
            if priority[status] == worst
        ]
        self.state.status = statuses[0][0]
        self.state.reasons = sorted({reason for _status, reason in statuses if reason is not None})

    def _symbol_quality(self, symbol: str) -> tuple[QualityStatus, tuple[str, ...]]:
        relevant = {
            stream: self._stream_quality[stream]
            for stream in self._expected_by_symbol.get(symbol, frozenset())
        }
        if not relevant:
            return QualityStatus.DEGRADED, ("awaiting_first_valid_event",)
        priority = {
            QualityStatus.HEALTHY: 0,
            QualityStatus.DEGRADED: 1,
            QualityStatus.RECONNECTING: 2,
            QualityStatus.STALE: 3,
            QualityStatus.INVALID: 4,
        }
        worst = max(priority[status] for status, _reason in relevant.values())
        status = next(status for status, _reason in relevant.values() if priority[status] == worst)
        reasons = tuple(
            sorted(
                {
                    reason
                    for item_status, reason in relevant.values()
                    if priority[item_status] == worst and reason is not None
                }
            )
        )
        return status, reasons

    def symbol_quality(self, symbol: str) -> tuple[QualityStatus, tuple[str, ...]]:
        return self._symbol_quality(symbol)

    def last_sequence(self, event_type: str, symbol: str) -> int | None:
        return self._last_sequence.get((event_type, symbol))

    def bootstrap_continuity(
        self,
        session_id: str,
        watermarks: Mapping[str, StreamWatermark],
        last_sequences: Mapping[tuple[str, str], int],
        stream_statuses: Mapping[str, QualityStatus],
        closed_kline_opens: Mapping[tuple[str, str], datetime] | None = None,
    ) -> None:
        self._current_session = session_id
        self._confirmed_sessions.add(session_id)
        self.state.watermarks.update(watermarks)
        self._last_sequence.update(last_sequences)
        self._closed_kline_open.update(closed_kline_opens or {})
        for stream, watermark in watermarks.items():
            if stream in self._expected_streams:
                self._stream_received_at[stream] = watermark.observed_at
        for stream, status in stream_statuses.items():
            if stream in self._expected_streams:
                reason = None if status is QualityStatus.HEALTHY else "restart_restored"
                self._stream_quality[stream] = (status, reason)
        self._refresh_state()

    def evaluate_freshness(self, now: datetime) -> None:
        for stream in self._expected_streams:
            observed_at = self._stream_received_at.get(stream)
            if observed_at is None:
                continue
            maximum_age = timedelta(seconds=90 if "@kline_" in stream else 5)
            current = self._stream_quality[stream]
            if now - observed_at > maximum_age and current != (
                QualityStatus.STALE,
                "freshness_exceeded",
            ):
                self._quality(QualityStatus.STALE, "freshness_exceeded", now, None, stream)
        self._refresh_state()

    def mark_global_failure(
        self, status: QualityStatus, reason: str, observed_at: datetime
    ) -> None:
        priority = {
            QualityStatus.HEALTHY: 0,
            QualityStatus.DEGRADED: 1,
            QualityStatus.RECONNECTING: 2,
            QualityStatus.STALE: 3,
            QualityStatus.INVALID: 4,
        }
        for stream in sorted(self._expected_streams):
            current_status, _current_reason = self._stream_quality[stream]
            if priority[current_status] > priority[status]:
                continue
            self._quality(status, reason, observed_at, None, stream)

    def _quality(
        self,
        status: QualityStatus,
        reason: str,
        observed_at: datetime,
        raw_event_id: str | None = None,
        stream: str = "market_data",
    ) -> None:
        self._stream_quality[stream] = (status, reason)
        self._refresh_state()
        event = QualityEvent(status, reason, stream, observed_at, raw_event_id)
        self.quality_events.append(event)
        try:
            self.store.append_quality(event)
        except Exception:
            self._stream_quality[stream] = (QualityStatus.INVALID, "quality_append_failed")
            self._refresh_state()

    def ingest(
        self,
        session_id: str,
        stream: str,
        payload: object,
        received_at: datetime,
    ) -> IngestResult:
        try:
            raw = make_raw_event(session_id, stream, payload, received_at)
        except (SchemaInvalid, ValueError):
            self._quality(QualityStatus.INVALID, "schema_invalid", received_at)
            return IngestResult(False, "schema_invalid", None, None)

        try:
            appended = self.store.append_raw(raw)
        except RawDedupeConflict:
            self._quality(
                QualityStatus.INVALID,
                "dedupe_conflict",
                received_at,
                None,
                raw.stream,
            )
            return IngestResult(False, "dedupe_conflict", raw, None)
        except Exception:
            self._quality(
                QualityStatus.INVALID,
                "raw_append_failed",
                received_at,
                None,
                raw.stream,
            )
            return IngestResult(False, "raw_append_failed", raw, None)
        if not appended:
            self.quality_events.append(
                QualityEvent(
                    QualityStatus.HEALTHY, "duplicate", "market_data", received_at, raw.raw_event_id
                )
            )
            return IngestResult(False, "duplicate", raw, None)

        return self._process_durable_raw(raw)

    def recover_raw(self, raw: RawMarketEvent) -> IngestResult:
        has_normalized = getattr(self.store, "has_normalized", None)
        if callable(has_normalized) and bool(has_normalized(raw.raw_event_id)):
            return IngestResult(False, "duplicate", raw, None)
        return self._process_durable_raw(raw)

    def _process_durable_raw(self, raw: RawMarketEvent) -> IngestResult:
        received_at = raw.received_at
        session_id = raw.collector_session_id
        if is_server_shutdown(raw):
            self._quality(
                QualityStatus.RECONNECTING,
                "server_shutdown",
                received_at,
                raw.raw_event_id,
                raw.stream,
            )
            return IngestResult(False, "server_shutdown", raw, None)

        if self._current_session is None:
            self._current_session = session_id
            self._confirmed_sessions.add(session_id)
        elif session_id != self._current_session and session_id not in self._confirmed_sessions:
            self._quality(
                QualityStatus.DEGRADED,
                "continuity_unverified",
                received_at,
                raw.raw_event_id,
                raw.stream,
            )
            return IngestResult(False, "continuity_unverified", raw, None)

        try:
            normalized = normalize(raw)
        except SchemaInvalid:
            self._quality(
                QualityStatus.INVALID, "schema_invalid", received_at, raw.raw_event_id, raw.stream
            )
            return IngestResult(False, "schema_invalid", raw, None)

        sequence_key = (normalized.event_type, normalized.symbol)
        previous = self._last_sequence.get(sequence_key)
        if normalized.event_type in {"trade", "book_ticker"} and previous is not None:
            if normalized.sequence <= previous:
                self._quality(
                    QualityStatus.DEGRADED,
                    "out_of_order",
                    received_at,
                    raw.raw_event_id,
                    raw.stream,
                )
                return IngestResult(False, "out_of_order", raw, None)
            if normalized.event_type == "trade" and normalized.sequence > previous + 1:
                self._quality(
                    QualityStatus.STALE,
                    "sequence_gap",
                    received_at,
                    raw.raw_event_id,
                    raw.stream,
                )
                return IngestResult(False, "sequence_gap", raw, None)

        closed_kline_key: tuple[str, str] | None = None
        closed_kline_open: datetime | None = None
        if normalized.event_type == "kline" and normalized.payload.get("closed") is True:
            interval = normalized.payload.get("interval")
            open_time = normalized.payload.get("open_time")
            if isinstance(interval, str) and isinstance(open_time, str):
                closed_kline_key = (normalized.symbol, interval)
                closed_kline_open = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
                previous_open = self._closed_kline_open.get(closed_kline_key)
                interval_delta = {
                    "1m": timedelta(minutes=1),
                    "5m": timedelta(minutes=5),
                    "1h": timedelta(hours=1),
                    "4h": timedelta(hours=4),
                }[interval]
                if (
                    previous_open is not None
                    and closed_kline_open != previous_open + interval_delta
                ):
                    reason = "out_of_order" if closed_kline_open <= previous_open else "kline_gap"
                    status = (
                        QualityStatus.DEGRADED if reason == "out_of_order" else QualityStatus.STALE
                    )
                    self._quality(status, reason, received_at, raw.raw_event_id, raw.stream)
                    return IngestResult(False, reason, raw, None)

        self._stream_quality[raw.stream] = (QualityStatus.HEALTHY, None)
        self._stream_received_at[raw.stream] = received_at
        self._refresh_state()
        symbol_status, symbol_reasons = self._symbol_quality(normalized.symbol)
        normalized = replace(
            normalized,
            quality_status=symbol_status,
            quality_reasons=symbol_reasons,
        )
        try:
            self.store.append_normalized(normalized)
        except Exception:
            self._quality(
                QualityStatus.INVALID,
                "normalized_append_failed",
                received_at,
                raw.raw_event_id,
                raw.stream,
            )
            return IngestResult(False, "normalized_append_failed", raw, None)
        self._last_sequence[sequence_key] = normalized.sequence
        self.state.watermarks[raw.stream] = normalized.stream_watermark
        if closed_kline_key is not None and closed_kline_open is not None:
            self._closed_kline_open[closed_kline_key] = closed_kline_open
        return IngestResult(True, "accepted", raw, normalized)
