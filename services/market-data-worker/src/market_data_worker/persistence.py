"""Postgres persistence for public market history and read projections."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.types.json import Jsonb

from .capabilities import PUBLIC_ALLOWLIST_VERSION
from .pipeline import RawDedupeConflict
from .types import NormalizedMarketEvent, QualityEvent, RawMarketEvent


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _event_payload(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    occurred_at: datetime,
    correlation_id: str,
    causation_id: str | None,
    data: dict[str, object],
) -> tuple[str, str, dict[str, object]]:
    event_id = str(uuid5(NAMESPACE_URL, f"woozoo:{event_type}:{aggregate_id}:{aggregate_version}"))
    payload_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload: dict[str, object] = {
        "spec_version": "woozoo.event/v1",
        "event_id": event_id,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": _iso(occurred_at),
        "published_at": None,
        "producer": "market-data-worker",
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "aggregate": {
            "type": aggregate_type,
            "id": aggregate_id,
            "version": aggregate_version,
        },
        "data": data,
        "payload_hash": payload_hash,
    }
    return event_id, payload_hash, payload


def _append_outbox(
    connection: psycopg.Connection[tuple[object, ...]],
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    occurred_at: datetime,
    correlation_id: str,
    causation_id: str | None,
    data: dict[str, object],
) -> None:
    event_id, payload_hash, payload = _event_payload(
        event_type,
        aggregate_type,
        aggregate_id,
        aggregate_version,
        occurred_at,
        correlation_id,
        causation_id,
        data,
    )
    connection.execute(
        """
        INSERT INTO outbox_events
            (event_id, event_type, payload, payload_hash, occurred_at, published_at)
        VALUES (%s,%s,%s,%s,%s,NULL)
        ON CONFLICT (event_id) DO NOTHING
        """,
        (event_id, event_type, Jsonb(payload), payload_hash, occurred_at),
    )


class PostgresMarketStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_session(self, session_id: str, connection_id: str, started_at: datetime) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO collector_sessions
                    (id, source, connection_id, allowlist_version, status, started_at)
                VALUES (%s, 'binance_spot_public', %s, %s, 'degraded', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (session_id, connection_id, PUBLIC_ALLOWLIST_VERSION, started_at),
            )

    def append_raw(self, event: RawMarketEvent) -> bool:
        kind = "control" if event.stream == "!serverShutdown" else event.record_kind
        with psycopg.connect(self._database_url) as connection:
            cursor = connection.execute(
                """
                INSERT INTO raw_market_events
                    (id, collector_session_id, source, stream, symbol, record_kind,
                     parent_raw_event_id, source_dedupe_key, payload_bytes, payload_hash,
                     source_event_time, received_at, ingested_at, sequence, schema_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    event.raw_event_id,
                    event.collector_session_id,
                    event.source,
                    event.stream,
                    event.symbol,
                    kind,
                    event.parent_raw_event_id,
                    event.source_dedupe_key,
                    event.payload_bytes,
                    event.payload_hash,
                    event.source_event_time,
                    event.received_at,
                    event.ingested_at,
                    event.sequence,
                    event.schema_version,
                ),
            )
            if cursor.rowcount == 1:
                classification = (
                    "operational_control"
                    if kind == "control"
                    else "quarantined"
                    if event.symbol is None
                    else "market"
                )
                _append_outbox(
                    connection,
                    "market.raw.appended.v1",
                    "raw_market_event",
                    event.raw_event_id,
                    1,
                    event.received_at,
                    event.collector_session_id,
                    event.parent_raw_event_id,
                    {
                        "raw_event_id": event.raw_event_id,
                        "collector_session_id": event.collector_session_id,
                        "source": event.source,
                        "stream": event.stream,
                        "symbol": event.symbol,
                        "source_dedupe_key": event.source_dedupe_key,
                        "source_event_time": _iso(event.source_event_time),
                        "received_at": _iso(event.received_at),
                        "raw_payload_hash": event.payload_hash,
                        "classification": classification,
                    },
                )
                return True
            existing = connection.execute(
                """
                SELECT payload_hash
                FROM raw_market_events
                WHERE source=%s AND stream=%s AND symbol IS NOT DISTINCT FROM %s
                  AND source_dedupe_key=%s
                """,
                (event.source, event.stream, event.symbol, event.source_dedupe_key),
            ).fetchone()
            if existing is None or existing[0] != event.payload_hash:
                raise RawDedupeConflict("source identity payload hash mismatch")
            return False

    def append_normalized(self, event: NormalizedMarketEvent) -> None:
        watermark = {
            "session_id": event.stream_watermark.session_id,
            "stream": event.stream_watermark.stream,
            "last_sequence": event.stream_watermark.last_sequence,
            "observed_at": event.stream_watermark.observed_at.isoformat().replace("+00:00", "Z"),
        }
        price = event.payload.get("price") or event.payload.get("close")
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO normalized_market_events
                    (id, raw_event_id, event_type, schema_version, source, symbol, event_time,
                     received_at, sequence, raw_payload_hash, correlation_id, quality_status,
                     quality_reasons, stream_watermark, payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    event.event_id,
                    event.raw_event_id,
                    event.event_type,
                    event.schema_version,
                    event.source,
                    event.symbol,
                    event.event_time,
                    event.received_at,
                    event.sequence,
                    event.raw_payload_hash,
                    event.correlation_id,
                    event.quality_status.value,
                    Jsonb(list(event.quality_reasons)),
                    Jsonb(watermark),
                    Jsonb(event.payload),
                ),
            )
            connection.execute(
                """
                INSERT INTO stream_watermark_projections
                    (collector_session_id, stream, last_sequence, observed_at, quality_status)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (collector_session_id, stream) DO UPDATE SET
                    last_sequence=EXCLUDED.last_sequence,
                    observed_at=EXCLUDED.observed_at,
                    quality_status=EXCLUDED.quality_status
                WHERE stream_watermark_projections.observed_at <= EXCLUDED.observed_at
                """,
                (
                    event.stream_watermark.session_id,
                    event.stream_watermark.stream,
                    event.stream_watermark.last_sequence,
                    event.stream_watermark.observed_at,
                    "healthy",
                ),
            )
            if isinstance(price, str):
                connection.execute(
                    """
                    INSERT INTO market_status_projections
                        (symbol, price, event_time, received_at, quality_status,
                         quality_reasons, stream_watermark, last_event_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        price=EXCLUDED.price,
                        event_time=EXCLUDED.event_time,
                        received_at=EXCLUDED.received_at,
                        quality_status=EXCLUDED.quality_status,
                        quality_reasons=EXCLUDED.quality_reasons,
                        stream_watermark=EXCLUDED.stream_watermark,
                        last_event_id=EXCLUDED.last_event_id
                    WHERE market_status_projections.event_time <= EXCLUDED.event_time
                    """,
                    (
                        event.symbol,
                        price,
                        event.event_time,
                        event.received_at,
                        event.quality_status.value,
                        Jsonb(list(event.quality_reasons)),
                        Jsonb(watermark),
                        event.event_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE market_status_projections
                    SET quality_status=%s,
                        quality_reasons=%s,
                        stream_watermark=%s
                    WHERE symbol=%s AND received_at <= %s
                    """,
                    (
                        event.quality_status.value,
                        Jsonb(list(event.quality_reasons)),
                        Jsonb(watermark),
                        event.symbol,
                        event.received_at,
                    ),
                )
            _append_outbox(
                connection,
                "market.normalized.recorded.v1",
                "normalized_market_event",
                event.event_id,
                max(1, event.sequence),
                event.received_at,
                event.correlation_id,
                event.raw_event_id,
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "schema_version": event.schema_version,
                    "source": event.source,
                    "symbol": event.symbol,
                    "event_time": _iso(event.event_time),
                    "received_at": _iso(event.received_at),
                    "sequence": event.sequence,
                    "raw_event_id": event.raw_event_id,
                    "raw_payload_hash": event.raw_payload_hash,
                    "correlation_id": event.correlation_id,
                    "quality_status": event.quality_status.value,
                    "quality_reasons": list(event.quality_reasons),
                    "stream_watermark": watermark,
                    "payload": event.payload,
                },
            )

    def append_quality(self, event: QualityEvent) -> None:
        identity = json.dumps(
            [event.scope, event.reason, event.observed_at.isoformat(), event.raw_event_id],
            separators=(",", ":"),
        )
        event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        with psycopg.connect(self._database_url) as connection:
            previous_status = "degraded"
            correlation_id = event.scope
            if event.raw_event_id is not None:
                previous = connection.execute(
                    """
                    SELECT COALESCE(watermark.quality_status, 'degraded'), session.id::text
                    FROM raw_market_events AS raw
                    JOIN collector_sessions AS session ON session.id=raw.collector_session_id
                    LEFT JOIN stream_watermark_projections AS watermark
                      ON watermark.collector_session_id=session.id
                     AND watermark.stream=raw.stream
                    WHERE raw.id=%s
                    """,
                    (event.raw_event_id,),
                ).fetchone()
                if previous is not None:
                    previous_status = str(previous[0])
                    correlation_id = str(previous[1])
            else:
                previous = connection.execute(
                    """
                    SELECT COALESCE(watermark.quality_status, 'degraded'), session.id::text
                    FROM collector_sessions AS session
                    LEFT JOIN stream_watermark_projections AS watermark
                      ON watermark.collector_session_id=session.id
                     AND watermark.stream=%s
                    WHERE session.source='binance_spot_public'
                    ORDER BY session.started_at DESC
                    LIMIT 1
                    """,
                    (event.scope,),
                ).fetchone()
                if previous is not None:
                    previous_status = str(previous[0])
                    correlation_id = str(previous[1])
            connection.execute(
                """
                INSERT INTO data_quality_events
                    (id, scope, status, reason, observed_at, raw_event_id)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    event_id,
                    event.scope,
                    event.status.value,
                    event.reason,
                    event.observed_at,
                    event.raw_event_id,
                ),
            )
            if event.raw_event_id is not None:
                connection.execute(
                    """
                    UPDATE stream_watermark_projections AS watermark
                    SET quality_status=%s
                    FROM raw_market_events AS raw
                    WHERE raw.id=%s
                      AND watermark.collector_session_id=raw.collector_session_id
                      AND watermark.stream=raw.stream
                    """,
                    (event.status.value, event.raw_event_id),
                )
                connection.execute(
                    """
                    UPDATE market_status_projections AS projection
                    SET quality_status=%s, quality_reasons=%s
                    FROM raw_market_events AS raw
                    WHERE raw.id=%s
                      AND raw.symbol=projection.symbol
                      AND projection.received_at <= %s
                    """,
                    (
                        event.status.value,
                        Jsonb([event.reason]),
                        event.raw_event_id,
                        event.observed_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE collector_sessions AS session
                    SET status=%s
                    FROM raw_market_events AS raw
                    WHERE raw.id=%s AND raw.collector_session_id=session.id
                    """,
                    (event.status.value, event.raw_event_id),
                )
            else:
                symbol = event.scope.partition("@")[0].upper()
                if symbol in {"BTCUSDT", "ETHUSDT"}:
                    connection.execute(
                        """
                        UPDATE market_status_projections
                        SET quality_status=%s, quality_reasons=%s
                        WHERE symbol=%s AND received_at <= %s
                        """,
                        (
                            event.status.value,
                            Jsonb([event.reason]),
                            symbol,
                            event.observed_at,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE collector_sessions
                    SET status=%s
                    WHERE id=(
                        SELECT id FROM collector_sessions
                        WHERE source='binance_spot_public'
                        ORDER BY started_at DESC LIMIT 1
                    )
                    """,
                    (event.status.value,),
                )
                connection.execute(
                    """
                    UPDATE stream_watermark_projections
                    SET quality_status=%s
                    WHERE collector_session_id=(
                        SELECT id FROM collector_sessions
                        WHERE source='binance_spot_public'
                        ORDER BY started_at DESC LIMIT 1
                    ) AND stream=%s
                    """,
                    (event.status.value, event.scope),
                )
            _append_outbox(
                connection,
                "market.quality.changed.v1",
                "data_quality_event",
                event_id,
                1,
                event.observed_at,
                correlation_id,
                event.raw_event_id,
                {
                    "quality_event_id": event_id,
                    "scope": event.scope,
                    "previous_status": previous_status,
                    "new_status": event.status.value,
                    "reason_codes": [event.reason],
                    "observed_at": _iso(event.observed_at),
                    "raw_event_id": event.raw_event_id,
                    "recovery_required": event.status.value != "healthy",
                },
            )
