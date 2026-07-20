"""Postgres boundary for immutable Phase 3 Evidence.

The repository deliberately exposes no update/delete operation.  A snapshot, all
feature observations, provenance rows, and its outbox event are committed in one
transaction so a retry can only observe either zero or one durable effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.types.json import Jsonb

from .builder import build_evidence_snapshot
from .canonical import CanonicalValue, canonical_digest, decimal_text, iso_utc
from .types import Candle, EvidenceSnapshot


@dataclass(frozen=True, slots=True)
class EvidenceCommandResult:
    evidence_id: str
    evidence_digest: str
    created: bool


def _outbox_payload(snapshot: EvidenceSnapshot, occurred_at: datetime) -> dict[str, object]:
    data: dict[str, object] = {
        "evidence_id": snapshot.evidence_id,
        "evidence_digest": snapshot.evidence_digest,
        "symbol": snapshot.symbol,
        "as_of": iso_utc(snapshot.as_of),
        "knowledge_cutoff": iso_utc(snapshot.knowledge_cutoff),
        "recipe_version": snapshot.recipe_version,
        "input_digest": snapshot.input_digest,
        "quality": snapshot.quality,
        "quality_reasons": list(snapshot.quality_reasons),
        "collector_session_id": snapshot.collector_session_id,
        "watermark_digest": snapshot.watermark_digest,
        "items": [
            {
                "item_type": item.item_type,
                "item_id": item.item_id,
                "raw_event_id": item.raw_event_id,
                "raw_payload_hash": item.raw_payload_hash,
            }
            for item in snapshot.items
        ],
        "candles": [
            {
                "normalized_event_id": candle.normalized_event_id,
                "raw_event_id": candle.raw_event_id,
                "raw_payload_hash": candle.raw_payload_hash,
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
            }
            for candle in snapshot.candles
        ],
        "features": [
            {
                "feature_id": feature.feature_id,
                "name": feature.name,
                "definition_version": feature.definition_version,
                "interval": feature.interval,
                "feature_time": iso_utc(feature.feature_time),
                "value": feature.value_text,
                "input_digest": feature.input_digest,
            }
            for feature in snapshot.features
        ],
    }
    payload_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "spec_version": "woozoo.event/v1",
        "event_id": str(uuid5(NAMESPACE_URL, f"woozoo:evidence:{snapshot.evidence_id}")),
        "event_type": "evidence.snapshot.created.v1",
        "event_version": 1,
        "occurred_at": iso_utc(occurred_at),
        "published_at": None,
        "producer": "evidence-worker",
        "correlation_id": snapshot.collector_session_id,
        "causation_id": None,
        "aggregate": {"type": "evidence_snapshot", "id": snapshot.evidence_id, "version": 1},
        "data": data,
        "payload_hash": payload_hash,
    }


class PostgresEvidenceStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def select_eligible_candles(
        self,
        *,
        symbol: str,
        as_of: datetime,
        knowledge_cutoff: datetime,
        _connection: psycopg.Connection[Any] | None = None,
    ) -> tuple[Candle, ...]:
        """Read only source rows that were knowable at the requested dual cutoff."""
        if _connection is None:
            with psycopg.connect(self._database_url) as connection:
                return self.select_eligible_candles(
                    symbol=symbol,
                    as_of=as_of,
                    knowledge_cutoff=knowledge_cutoff,
                    _connection=connection,
                )
        query = """
            SELECT normalized.id, normalized.raw_event_id, normalized.raw_payload_hash,
                   normalized.symbol, normalized.event_time, normalized.received_at,
                   normalized.payload
            FROM normalized_market_events AS normalized
            JOIN raw_market_events AS raw ON raw.id=normalized.raw_event_id
            JOIN collector_sessions AS session ON session.id=raw.collector_session_id
            WHERE normalized.event_type='kline'
              AND normalized.schema_version='woozoo.market-event/v1'
              AND normalized.source='binance_spot_public'
              AND raw.source='binance_spot_public'
              AND raw.schema_version='woozoo.raw-market-event/v1'
              AND session.allowlist_version=
                  'binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5'
              AND normalized.symbol=%s
              AND normalized.event_time <= %s
              AND normalized.received_at <= %s
              AND normalized.payload->>'closed'='true'
              AND (normalized.payload->>'close_time')::timestamptz <= %s
            ORDER BY normalized.received_at, normalized.id
        """
        rows = _connection.execute(query, (symbol, as_of, knowledge_cutoff, as_of)).fetchall()
        candles: list[Candle] = []
        for event_id, raw_event_id, raw_hash, row_symbol, event_time, received_at, payload in rows:
            candles.append(
                Candle(
                    normalized_event_id=str(event_id),
                    raw_event_id=str(raw_event_id),
                    raw_payload_hash=str(raw_hash),
                    symbol=str(row_symbol),
                    interval=str(payload["interval"]),
                    event_time=event_time,
                    received_at=received_at,
                    open_time=datetime.fromisoformat(
                        str(payload["open_time"]).replace("Z", "+00:00")
                    ),
                    close_time=datetime.fromisoformat(
                        str(payload["close_time"]).replace("Z", "+00:00")
                    ),
                    open=Decimal(str(payload["open"])),
                    high=Decimal(str(payload["high"])),
                    low=Decimal(str(payload["low"])),
                    close=Decimal(str(payload["close"])),
                    base_volume=Decimal(str(payload["base_volume"])),
                    closed=True,
                )
            )
        return tuple(candles)

    def build_snapshot(
        self,
        *,
        symbol: str,
        as_of: datetime,
        knowledge_cutoff: datetime,
        _connection: psycopg.Connection[Any] | None = None,
    ) -> EvidenceSnapshot:
        """Build only from source-owned quality and watermark state.

        Callers cannot assert that data is healthy.  A provisional deterministic
        selection identifies the exact source rows, after which their durable
        quality, session, and watermark metadata are loaded and bound into the
        final snapshot.  The builder rejects any non-healthy state or reason.
        """
        if _connection is None:
            with psycopg.connect(self._database_url) as connection:
                return self.build_snapshot(
                    symbol=symbol,
                    as_of=as_of,
                    knowledge_cutoff=knowledge_cutoff,
                    _connection=connection,
                )
        candles = self.select_eligible_candles(
            symbol=symbol,
            as_of=as_of,
            knowledge_cutoff=knowledge_cutoff,
            _connection=_connection,
        )
        provisional = build_evidence_snapshot(
            candles,
            symbol=symbol,
            as_of=as_of,
            knowledge_cutoff=knowledge_cutoff,
            quality="healthy",
            quality_reasons=(),
            collector_session_id="00000000-0000-0000-0000-000000000000",
            watermark_digest="0" * 64,
        )
        selected_ids = [candle.normalized_event_id for candle in provisional.candles]
        selected_raw_ids = [candle.raw_event_id for candle in provisional.candles]
        selected_scopes = sorted(
            {
                "market_data",
                *(
                    f"{candle.symbol.lower()}@kline_{candle.interval}"
                    for candle in provisional.candles
                ),
            }
        )
        rows = _connection.execute(
            """
            SELECT normalized.id, normalized.quality_status,
                   normalized.quality_reasons, normalized.stream_watermark,
                   raw.collector_session_id, normalized.received_at
            FROM normalized_market_events AS normalized
            JOIN raw_market_events AS raw ON raw.id=normalized.raw_event_id
            WHERE normalized.id = ANY(%s)
            """,
            (selected_ids,),
        ).fetchall()
        quality_rows = _connection.execute(
            """
            SELECT DISTINCT ON (scope) scope, status, reason, observed_at, sequence_no
            FROM data_quality_events
            WHERE observed_at <= %s
              AND (lower(scope) = ANY(%s) OR raw_event_id = ANY(%s))
            ORDER BY scope, observed_at DESC, sequence_no DESC
            """,
            (knowledge_cutoff, selected_scopes, selected_raw_ids),
        ).fetchall()
        if len(rows) != len(selected_ids):
            raise RuntimeError("selected Evidence provenance is incomplete")
        by_id = {str(row[0]): row for row in rows}
        if set(by_id) != set(selected_ids):
            raise RuntimeError("selected Evidence provenance identity mismatch")
        ordered_metadata: list[CanonicalValue] = []
        quality_reasons: set[str] = set()
        quality = "healthy"
        quality_rank = {
            "healthy": 0,
            "degraded": 1,
            "reconnecting": 2,
            "stale": 3,
            "invalid": 4,
        }
        latest_session: tuple[datetime, str, str] | None = None
        for normalized_id in selected_ids:
            row = by_id[normalized_id]
            row_quality = str(row[1])
            if row_quality not in quality_rank:
                raise RuntimeError("selected Evidence has an unknown quality state")
            if quality_rank[row_quality] > quality_rank[quality]:
                quality = row_quality
            row_reasons = cast(list[CanonicalValue], sorted(str(reason) for reason in row[2]))
            quality_reasons.update(str(reason) for reason in row_reasons)
            session_id = str(row[4])
            received_at = row[5]
            session_key = (received_at, normalized_id, session_id)
            if latest_session is None or session_key > latest_session:
                latest_session = session_key
            ordered_metadata.append(
                {
                    "normalized_event_id": normalized_id,
                    "quality": row_quality,
                    "quality_reasons": row_reasons,
                    "stream_watermark": row[3],
                    "collector_session_id": session_id,
                }
            )
        for scope, status, reason, observed_at, sequence_no in quality_rows:
            historical_quality = str(status)
            if historical_quality not in quality_rank:
                raise RuntimeError("Evidence quality history has an unknown state")
            if quality_rank[historical_quality] > quality_rank[quality]:
                quality = historical_quality
            if historical_quality != "healthy":
                quality_reasons.add(str(reason))
            ordered_metadata.append(
                {
                    "quality_scope": str(scope),
                    "quality": historical_quality,
                    "reason": str(reason),
                    "observed_at": iso_utc(observed_at),
                    "sequence_no": int(sequence_no),
                }
            )
        assert latest_session is not None
        watermark_digest = canonical_digest({"selected_source_state": ordered_metadata})
        return build_evidence_snapshot(
            candles,
            symbol=symbol,
            as_of=as_of,
            knowledge_cutoff=knowledge_cutoff,
            quality=quality,
            quality_reasons=tuple(sorted(quality_reasons)),
            collector_session_id=latest_session[2],
            watermark_digest=watermark_digest,
        )

    def append_snapshot(
        self,
        snapshot: EvidenceSnapshot,
        *,
        created_at: datetime,
        _connection: psycopg.Connection[Any] | None = None,
    ) -> bool:
        """Atomically append a snapshot and event; return False for an exact retry."""
        if _connection is None:
            with psycopg.connect(self._database_url) as connection:
                return self.append_snapshot(snapshot, created_at=created_at, _connection=connection)
        authoritative = self.build_snapshot(
            symbol=snapshot.symbol,
            as_of=snapshot.as_of,
            knowledge_cutoff=snapshot.knowledge_cutoff,
            _connection=_connection,
        )
        if authoritative != snapshot:
            raise ValueError("Evidence snapshot does not match durable source authority")
        event = _outbox_payload(snapshot, created_at)
        connection = _connection
        if connection is not None:
            inserted = connection.execute(
                """
                INSERT INTO evidence_snapshots
                    (evidence_id, evidence_digest, symbol, as_of, knowledge_cutoff,
                     recipe_version, input_digest, quality_status, quality_reasons,
                     collector_session_id, watermark_digest, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (evidence_id) DO NOTHING
                """,
                (
                    snapshot.evidence_id,
                    snapshot.evidence_digest,
                    snapshot.symbol,
                    snapshot.as_of,
                    snapshot.knowledge_cutoff,
                    snapshot.recipe_version,
                    snapshot.input_digest,
                    snapshot.quality,
                    Jsonb(list(snapshot.quality_reasons)),
                    snapshot.collector_session_id,
                    snapshot.watermark_digest,
                    created_at,
                ),
            ).rowcount
            if inserted == 0:
                existing = connection.execute(
                    "SELECT evidence_digest FROM evidence_snapshots WHERE evidence_id=%s",
                    (snapshot.evidence_id,),
                ).fetchone()
                if existing is None or str(existing[0]) != snapshot.evidence_digest:
                    raise RuntimeError("Evidence identity conflicts with a different digest")
                return False
            for feature in snapshot.features:
                connection.execute(
                    """
                    INSERT INTO feature_observations
                        (id, symbol, interval, feature_name, feature_version, as_of,
                         value_text, input_digest, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        feature.feature_id,
                        feature.symbol,
                        feature.interval,
                        feature.name,
                        feature.definition_version,
                        feature.feature_time,
                        feature.value_text,
                        feature.input_digest,
                        created_at,
                    ),
                )
                for ordinal, values in enumerate(
                    zip(
                        feature.input_normalized_event_ids,
                        feature.input_raw_event_ids,
                        feature.input_raw_payload_hashes,
                        strict=True,
                    )
                ):
                    normalized_id, raw_id, raw_hash = values
                    connection.execute(
                        """
                        INSERT INTO feature_observation_inputs
                            (feature_observation_id, ordinal, normalized_event_id,
                             raw_event_id, raw_payload_hash)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (feature_observation_id, ordinal) DO NOTHING
                        """,
                        (feature.feature_id, ordinal, normalized_id, raw_id, raw_hash),
                    )
            for ordinal, item in enumerate(snapshot.items):
                connection.execute(
                    """
                    INSERT INTO evidence_items
                        (evidence_id, ordinal, item_type, item_id, normalized_event_id,
                         feature_observation_id, raw_event_id, raw_payload_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        snapshot.evidence_id,
                        ordinal,
                        item.item_type,
                        item.item_id,
                        item.item_id if item.item_type == "normalized_market_event" else None,
                        item.item_id if item.item_type == "feature_observation" else None,
                        item.raw_event_id,
                        item.raw_payload_hash,
                    ),
                )
            connection.execute(
                """
                INSERT INTO outbox_events
                    (event_id, event_type, payload, payload_hash, occurred_at, published_at)
                VALUES (%s,%s,%s,%s,%s,NULL)
                """,
                (
                    event["event_id"],
                    event["event_type"],
                    Jsonb(event),
                    event["payload_hash"],
                    created_at,
                ),
            )
        return True

    def materialize_command(
        self,
        *,
        idempotency_key: str,
        service_principal: str,
        symbol: str,
        as_of: datetime,
        knowledge_cutoff: datetime,
        created_at: datetime,
    ) -> EvidenceCommandResult:
        if service_principal != "internal-evidence-scheduler":
            raise ValueError("CALLER_UNAUTHORIZED")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Idempotency-Key must contain 1..128 characters")
        request_hash = canonical_digest(
            {
                "contract_version": "woozoo.evidence-command/v1",
                "method": "POST",
                "path": "/api/v1/commands/evidence-snapshots",
                "service_principal": service_principal,
                "body": {
                    "symbol": symbol,
                    "as_of": iso_utc(as_of),
                    "knowledge_cutoff": iso_utc(knowledge_cutoff),
                },
            }
        )
        with psycopg.connect(self._database_url) as lock_connection:
            lock_connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (idempotency_key,),
            )
            receipt = lock_connection.execute(
                """
                SELECT receipt.request_hash, receipt.evidence_id, snapshot.evidence_digest
                FROM evidence_command_receipts AS receipt
                JOIN evidence_snapshots AS snapshot
                  ON snapshot.evidence_id=receipt.evidence_id
                WHERE receipt.idempotency_key=%s
                """,
                (idempotency_key,),
            ).fetchone()
            if receipt is not None:
                if str(receipt[0]) != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return EvidenceCommandResult(str(receipt[1]), str(receipt[2]), False)
            snapshot = self.build_snapshot(
                symbol=symbol,
                as_of=as_of,
                knowledge_cutoff=knowledge_cutoff,
                _connection=lock_connection,
            )
            inserted = self.append_snapshot(
                snapshot,
                created_at=created_at,
                _connection=lock_connection,
            )
            lock_connection.execute(
                """
                INSERT INTO evidence_command_receipts
                    (idempotency_key, request_hash, evidence_id, created_at)
                VALUES (%s,%s,%s,%s)
                """,
                (idempotency_key, request_hash, snapshot.evidence_id, created_at),
            )
            return EvidenceCommandResult(
                snapshot.evidence_id,
                snapshot.evidence_digest,
                inserted,
            )
