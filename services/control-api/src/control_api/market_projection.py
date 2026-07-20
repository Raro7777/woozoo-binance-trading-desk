"""Read-only market status projection for the Phase 2 control API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

import psycopg


class MarketProjectionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MarketStatusSnapshot:
    symbol: str
    price: str
    event_time: datetime
    received_at: datetime
    quality: str
    quality_reasons: tuple[str, ...]
    session_id: str
    stream: str
    last_sequence: int
    watermark_observed_at: datetime


class MarketStatusProjection:
    def get(self, symbol: str) -> MarketStatusSnapshot | None:
        raise NotImplementedError


class StaticMarketStatusProjection(MarketStatusProjection):
    def __init__(self, snapshots: Mapping[str, MarketStatusSnapshot]) -> None:
        self._snapshots = dict(snapshots)
        self.requests: list[str] = []

    def get(self, symbol: str) -> MarketStatusSnapshot | None:
        self.requests.append(symbol)
        return self._snapshots.get(symbol)


class PostgresMarketStatusProjection(MarketStatusProjection):
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get(self, symbol: str) -> MarketStatusSnapshot | None:
        query = """
            SELECT symbol, price, event_time, received_at, quality_status, quality_reasons,
                   stream_watermark ->> 'session_id', stream_watermark ->> 'stream',
                   (stream_watermark ->> 'last_sequence')::bigint,
                   (stream_watermark ->> 'observed_at')::timestamptz
            FROM market_status_reader_v1
            WHERE symbol = %s
        """
        try:
            with psycopg.connect(self._database_url, connect_timeout=1) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, (symbol,))
                    row = cursor.fetchone()
        except psycopg.Error as error:
            raise MarketProjectionUnavailable("market status projection is unavailable") from error
        if row is None:
            return None
        return MarketStatusSnapshot(
            symbol=str(row[0]),
            price=str(row[1]),
            event_time=row[2],
            received_at=row[3],
            quality=str(row[4]),
            quality_reasons=tuple(str(reason) for reason in row[5]),
            session_id=str(row[6]),
            stream=str(row[7]),
            last_sequence=int(row[8]),
            watermark_observed_at=row[9],
        )
