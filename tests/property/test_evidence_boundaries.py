from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib

import pytest

from evidence_worker.builder import build_evidence_snapshot
from evidence_worker.types import Candle


BASE = datetime(2026, 7, 19, tzinfo=UTC)
SESSION_ID = "018f7000-0000-7000-8000-000000000001"


def source_id(kind: str, identity: object) -> str:
    return hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()


def candle(identity: str, open_minute: int, event_time: datetime, received_at: datetime) -> Candle:
    open_time = BASE + timedelta(minutes=open_minute)
    return Candle(
        normalized_event_id=source_id("normalized", identity),
        raw_event_id=source_id("raw", identity),
        raw_payload_hash=(identity.encode().hex() + "0" * 64)[:64],
        symbol="BTCUSDT",
        interval="1m",
        event_time=event_time,
        received_at=received_at,
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        base_volume=Decimal("10"),
        closed=True,
    )


@pytest.mark.parametrize(
    ("event_delta", "received_delta", "included"),
    [
        (timedelta(microseconds=-1), timedelta(microseconds=-1), True),
        (timedelta(0), timedelta(0), True),
        (timedelta(microseconds=1), timedelta(microseconds=-1), False),
        (timedelta(microseconds=-1), timedelta(microseconds=1), False),
    ],
)
def test_dual_cutoff_is_independently_inclusive(
    event_delta: timedelta,
    received_delta: timedelta,
    included: bool,
) -> None:
    as_of = BASE + timedelta(minutes=22)
    cutoff = BASE + timedelta(minutes=30)
    baseline = tuple(
        candle(
            f"base-{index}",
            index,
            BASE + timedelta(minutes=index, seconds=59),
            BASE + timedelta(minutes=index + 1),
        )
        for index in range(1, 22)
    )
    boundary = candle("boundary", 21, as_of + event_delta, cutoff + received_delta)

    snapshot = build_evidence_snapshot(
        (*baseline, boundary),
        symbol="BTCUSDT",
        as_of=as_of,
        knowledge_cutoff=cutoff,
        quality="healthy",
        quality_reasons=(),
        collector_session_id=SESSION_ID,
        watermark_digest="b" * 64,
        required_intervals=("1m",),
    )

    ids = {item.normalized_event_id for item in snapshot.candles}
    assert (source_id("normalized", "boundary") in ids) is included
