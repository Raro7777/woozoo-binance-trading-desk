"""Postgres persistence for public market history and read projections."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
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


def market_projection_price(event_type: str, payload: Mapping[str, object]) -> str | None:
    """Return the authoritative display price without binary floating point."""

    direct = payload.get("price") or payload.get("close")
    if isinstance(direct, str):
        return direct
    if event_type != "book_ticker":
        return None
    bid = payload.get("bid_price")
    ask = payload.get("ask_price")
    if not isinstance(bid, str) or not isinstance(ask, str):
        return None
    return format((Decimal(bid) + Decimal(ask)) / Decimal(2), "f")


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
    _QUALITY_WRITE_ATTEMPTS = 3
    _QUALITY_RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def create_session(self, session_id: str, connection_id: str, started_at: datetime) -> None:
        with psycopg.connect(self._database_url) as connection:
            # Serialize market-authority writers first; the following table lock
            # then conflicts with the Phase 7 verifier's normalized-table SHARE.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('market-authority-v1',0))"
            )
            connection.execute("LOCK TABLE normalized_market_events IN ROW EXCLUSIVE MODE")
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
        price = market_projection_price(event.event_type, event.payload)
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('market-authority-v1',0))"
            )
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
        for attempt in range(self._QUALITY_WRITE_ATTEMPTS):
            try:
                self._append_quality_once(event)
                return
            except psycopg.Error as error:
                retryable = error.sqlstate in self._QUALITY_RETRYABLE_SQLSTATES
                if not retryable or attempt + 1 == self._QUALITY_WRITE_ATTEMPTS:
                    raise

    def _append_quality_once(self, event: QualityEvent) -> None:
        identity = json.dumps(
            [event.scope, event.reason, event.observed_at.isoformat(), event.raw_event_id],
            separators=(",", ":"),
        )
        event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        with psycopg.connect(self._database_url) as connection:
            # The writer role cannot take SHARE without broader table privilege.
            # A writer-only advisory fence serializes normalized/session/quality
            # writers; ROW EXCLUSIVE then conflicts with the verifier's SHARE.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('market-authority-v1',0))"
            )
            connection.execute("LOCK TABLE normalized_market_events IN ROW EXCLUSIVE MODE")
            previous_status = "degraded"
            correlation_id = event.scope
            collector_session_id: str | None = None
            stream = event.scope
            symbol: str | None = None
            if event.raw_event_id is not None:
                source = connection.execute(
                    """
                    SELECT raw.collector_session_id::text, raw.stream, raw.symbol
                    FROM raw_market_events AS raw
                    WHERE raw.id=%s
                    """,
                    (event.raw_event_id,),
                ).fetchone()
                if source is not None:
                    collector_session_id = str(source[0])
                    stream = str(source[1])
                    symbol = None if source[2] is None else str(source[2])
                    correlation_id = collector_session_id
            else:
                latest = connection.execute(
                    """
                    SELECT session.id::text
                    FROM collector_sessions AS session
                    WHERE session.source='binance_spot_public'
                    ORDER BY session.started_at DESC, session.id DESC
                    LIMIT 1
                    """,
                ).fetchone()
                if latest is not None:
                    collector_session_id = str(latest[0])
                    correlation_id = collector_session_id
                candidate_symbol = event.scope.partition("@")[0].upper()
                if candidate_symbol in {"BTCUSDT", "ETHUSDT"}:
                    symbol = candidate_symbol

            if collector_session_id is not None:
                watermark = connection.execute(
                    """
                    SELECT quality_status
                    FROM stream_watermark_projections
                    WHERE collector_session_id=%s AND stream=%s
                    FOR UPDATE
                    """,
                    (collector_session_id, stream),
                ).fetchone()
                if watermark is not None:
                    previous_status = str(watermark[0])
            if symbol is not None:
                connection.execute(
                    "SELECT 1 FROM market_status_projections WHERE symbol=%s FOR UPDATE",
                    (symbol,),
                ).fetchone()
            if collector_session_id is not None:
                connection.execute(
                    "SELECT 1 FROM collector_sessions WHERE id=%s FOR UPDATE",
                    (collector_session_id,),
                ).fetchone()
                latest_session = connection.execute(
                    """
                    SELECT id::text
                    FROM collector_sessions
                    WHERE source='binance_spot_public'
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                latest_session_id = None if latest_session is None else str(latest_session[0])
                authority_bound = latest_session_id == collector_session_id
            else:
                authority_bound = False

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
            if authority_bound:
                connection.execute(
                    """
                    UPDATE stream_watermark_projections
                    SET quality_status=%s
                    WHERE collector_session_id=%s AND stream=%s
                    """,
                    (event.status.value, collector_session_id, stream),
                )
            if authority_bound and symbol is not None:
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
            if authority_bound:
                connection.execute(
                    """
                    UPDATE collector_sessions
                    SET status=%s
                    WHERE id=%s
                    """,
                    (event.status.value, collector_session_id),
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
