"""Durable restart continuity and raw-first crash recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, cast

import psycopg

from .capabilities import fixed_phase_two_streams
from .pipeline import CollectorPipeline
from .types import QualityStatus, RawMarketEvent, StreamWatermark


@dataclass(frozen=True, slots=True)
class ContinuitySnapshot:
    session_id: str
    watermarks: Mapping[str, StreamWatermark]
    last_sequences: Mapping[tuple[str, str], int]
    stream_statuses: Mapping[str, QualityStatus]
    pending_raw: tuple[RawMarketEvent, ...]
    closed_kline_opens: Mapping[tuple[str, str], datetime] = field(default_factory=dict)


class ContinuityRepository(Protocol):
    def load(self) -> ContinuitySnapshot: ...


class RestartCoordinator:
    def __init__(self, repository: ContinuityRepository) -> None:
        self._repository = repository

    def restore(self, pipeline: CollectorPipeline) -> int:
        snapshot = self._repository.load()
        pipeline.bootstrap_continuity(
            snapshot.session_id,
            snapshot.watermarks,
            snapshot.last_sequences,
            snapshot.stream_statuses,
            snapshot.closed_kline_opens,
        )
        return sum(1 for raw in snapshot.pending_raw if pipeline.recover_raw(raw).accepted)


class PostgresRestartRepository:
    """Rebuild continuity exclusively from durable writer-role projections/history."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def load(self) -> ContinuitySnapshot:
        expected_streams = tuple(stream.name for stream in fixed_phase_two_streams())
        with psycopg.connect(self._database_url) as connection:
            session = connection.execute(
                """
                SELECT id::text
                FROM collector_sessions
                WHERE source='binance_spot_public'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            if session is None:
                raise RuntimeError("restart continuity requires a durable collector session")
            session_id = str(session[0])
            watermark_rows = connection.execute(
                """
                SELECT DISTINCT ON (stream)
                       collector_session_id::text, stream, last_sequence, observed_at,
                       quality_status
                FROM stream_watermark_projections
                WHERE stream = ANY(%s)
                ORDER BY stream, observed_at DESC
                """,
                (list(expected_streams),),
            ).fetchall()
            sequence_rows = connection.execute(
                """
                SELECT DISTINCT ON (event_type, symbol)
                       event_type, symbol, sequence
                FROM normalized_market_events
                ORDER BY event_type, symbol, received_at DESC, sequence DESC
                """
            ).fetchall()
            closed_kline_rows = connection.execute(
                """
                SELECT DISTINCT ON (symbol, payload->>'interval')
                       symbol, payload->>'interval', payload->>'open_time'
                FROM normalized_market_events
                WHERE event_type='kline' AND payload->>'closed'='true'
                ORDER BY symbol, payload->>'interval', received_at DESC
                """
            ).fetchall()
            pending_rows = connection.execute(
                """
                SELECT raw.id, raw.collector_session_id::text, raw.source, raw.stream,
                       raw.symbol, raw.source_event_time, raw.received_at, raw.ingested_at,
                       raw.sequence, raw.source_dedupe_key, raw.payload_bytes,
                       raw.payload_hash, raw.schema_version, raw.record_kind,
                       raw.parent_raw_event_id
                FROM raw_market_events AS raw
                LEFT JOIN normalized_market_events AS normalized
                  ON normalized.raw_event_id=raw.id
                WHERE normalized.id IS NULL
                  AND raw.stream = ANY(%s)
                  AND NOT EXISTS (
                      SELECT 1 FROM data_quality_events AS quality
                      WHERE quality.raw_event_id=raw.id
                        AND quality.reason <> 'normalized_append_failed'
                  )
                ORDER BY raw.received_at, raw.id
                """,
                (list(expected_streams),),
            ).fetchall()

        watermarks: dict[str, StreamWatermark] = {}
        statuses: dict[str, QualityStatus] = {}
        for row in watermark_rows:
            row_session, stream, sequence, observed_at, status = row
            stream_name = str(stream)
            watermarks[stream_name] = StreamWatermark(
                session_id=str(row_session),
                stream=stream_name,
                last_sequence=int(cast(int, sequence)),
                observed_at=cast(datetime, observed_at),
            )
            statuses[stream_name] = QualityStatus(str(status))
        last_sequences = {
            (str(event_type), str(symbol)): int(cast(int, sequence))
            for event_type, symbol, sequence in sequence_rows
        }
        closed_kline_opens = {
            (str(symbol), str(interval)): datetime.fromisoformat(
                str(open_time).replace("Z", "+00:00")
            )
            for symbol, interval, open_time in closed_kline_rows
        }
        pending = tuple(self._raw_from_row(row) for row in pending_rows)
        return ContinuitySnapshot(
            session_id,
            watermarks,
            last_sequences,
            statuses,
            pending,
            closed_kline_opens,
        )

    @staticmethod
    def _raw_from_row(row: tuple[object, ...]) -> RawMarketEvent:
        return RawMarketEvent(
            raw_event_id=str(row[0]),
            collector_session_id=str(row[1]),
            source=str(row[2]),
            stream=str(row[3]),
            symbol=None if row[4] is None else str(row[4]),
            source_event_time=cast(datetime | None, row[5]),
            received_at=cast(datetime, row[6]),
            ingested_at=cast(datetime, row[7]),
            sequence=None if row[8] is None else int(cast(int, row[8])),
            source_dedupe_key=str(row[9]),
            payload_bytes=bytes(cast(bytes, row[10])),
            payload_hash=str(row[11]),
            schema_version=str(row[12]),
            record_kind=str(row[13]),
            parent_raw_event_id=None if row[14] is None else str(row[14]),
        )
