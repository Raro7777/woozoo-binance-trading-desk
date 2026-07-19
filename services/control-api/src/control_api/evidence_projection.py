"""Read-only point-in-time Evidence projection for the Phase 3 control API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Mapping

import psycopg


EVIDENCE_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class EvidenceProjectionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceItemSnapshot:
    item_type: str
    item_id: str
    raw_event_id: str
    raw_payload_hash: str


@dataclass(frozen=True, slots=True)
class EvidenceCandleSnapshot:
    normalized_event_id: str
    raw_event_id: str
    raw_payload_hash: str
    interval: str
    event_time: datetime
    received_at: datetime
    open_time: str
    close_time: str
    open: str
    high: str
    low: str
    close: str
    base_volume: str


@dataclass(frozen=True, slots=True)
class EvidenceFeatureSnapshot:
    feature_id: str
    name: str
    definition_version: str
    interval: str
    feature_time: datetime
    value: str
    input_digest: str


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    evidence_id: str
    evidence_digest: str
    symbol: str
    as_of: datetime
    knowledge_cutoff: datetime
    recipe_version: str
    input_digest: str
    quality: str
    quality_reasons: tuple[str, ...]
    collector_session_id: str
    watermark_digest: str
    items: tuple[EvidenceItemSnapshot, ...]
    candles: tuple[EvidenceCandleSnapshot, ...]
    features: tuple[EvidenceFeatureSnapshot, ...]


class EvidenceProjection:
    def get(self, evidence_id: str) -> EvidenceSnapshot | None:
        raise NotImplementedError


class StaticEvidenceProjection(EvidenceProjection):
    def __init__(self, snapshots: Mapping[str, EvidenceSnapshot]) -> None:
        self._snapshots = dict(snapshots)
        self.requests: list[str] = []

    def get(self, evidence_id: str) -> EvidenceSnapshot | None:
        self.requests.append(evidence_id)
        return self._snapshots.get(evidence_id)


class PostgresEvidenceProjection(EvidenceProjection):
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get(self, evidence_id: str) -> EvidenceSnapshot | None:
        query = """
            SELECT evidence_id, evidence_digest, symbol, as_of, knowledge_cutoff,
                   recipe_version, input_digest, quality_status, quality_reasons,
                   collector_session_id, watermark_digest, item_type, item_id,
                   raw_event_id, raw_payload_hash
            FROM evidence_reader_v1
            WHERE evidence_id = %s
            ORDER BY item_ordinal
        """
        try:
            with psycopg.connect(self._database_url, connect_timeout=1) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, (evidence_id,))
                    rows = cursor.fetchall()
                    cursor.execute(
                        """
                        SELECT normalized_event_id, raw_event_id, raw_payload_hash,
                               event_time, received_at, payload
                        FROM evidence_candle_reader_v1
                        WHERE evidence_id=%s ORDER BY ordinal
                        """,
                        (evidence_id,),
                    )
                    candle_rows = cursor.fetchall()
                    cursor.execute(
                        """
                        SELECT feature_id, feature_name, feature_version, interval,
                               as_of, value_text, input_digest
                        FROM evidence_feature_reader_v1
                        WHERE evidence_id=%s ORDER BY ordinal
                        """,
                        (evidence_id,),
                    )
                    feature_rows = cursor.fetchall()
        except psycopg.Error as error:
            raise EvidenceProjectionUnavailable("evidence projection is unavailable") from error
        if not rows:
            return None
        first = rows[0]
        return EvidenceSnapshot(
            evidence_id=str(first[0]),
            evidence_digest=str(first[1]),
            symbol=str(first[2]),
            as_of=first[3],
            knowledge_cutoff=first[4],
            recipe_version=str(first[5]),
            input_digest=str(first[6]),
            quality=str(first[7]),
            quality_reasons=tuple(str(reason) for reason in first[8]),
            collector_session_id=str(first[9]),
            watermark_digest=str(first[10]),
            items=tuple(
                EvidenceItemSnapshot(
                    item_type=str(row[11]),
                    item_id=str(row[12]),
                    raw_event_id=str(row[13]),
                    raw_payload_hash=str(row[14]),
                )
                for row in rows
            ),
            candles=tuple(
                EvidenceCandleSnapshot(
                    normalized_event_id=str(row[0]),
                    raw_event_id=str(row[1]),
                    raw_payload_hash=str(row[2]),
                    event_time=row[3],
                    received_at=row[4],
                    interval=str(row[5]["interval"]),
                    open_time=str(row[5]["open_time"]),
                    close_time=str(row[5]["close_time"]),
                    open=str(row[5]["open"]),
                    high=str(row[5]["high"]),
                    low=str(row[5]["low"]),
                    close=str(row[5]["close"]),
                    base_volume=str(row[5]["base_volume"]),
                )
                for row in candle_rows
            ),
            features=tuple(
                EvidenceFeatureSnapshot(
                    feature_id=str(row[0]),
                    name=str(row[1]),
                    definition_version=str(row[2]),
                    interval=str(row[3]),
                    feature_time=row[4],
                    value=str(row[5]),
                    input_digest=str(row[6]),
                )
                for row in feature_rows
            ),
        )
