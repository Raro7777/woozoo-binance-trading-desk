from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
import hashlib

import pytest

import evidence_worker.builder as builder_module
from evidence_worker.builder import EvidenceBuildError, build_evidence_snapshot
from evidence_worker.persistence import _validate_source_authority
from evidence_worker.types import Candle


BASE = datetime(2026, 7, 19, tzinfo=UTC)
SESSION_ID = "018f7000-0000-7000-8000-000000000001"


def source_id(kind: str, identity: object) -> str:
    return hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()


def candle(
    minute: int,
    *,
    event_offset_seconds: int = 59,
    received_offset_seconds: int = 59,
    close_offset_seconds: int = 59,
    closed: bool = True,
    identity: str | None = None,
) -> Candle:
    open_time = BASE + timedelta(minutes=minute)
    key = identity or str(minute)
    return Candle(
        normalized_event_id=source_id("normalized", key),
        raw_event_id=source_id("raw", key),
        raw_payload_hash=f"{minute + 1:064x}",
        symbol="BTCUSDT",
        interval="1m",
        event_time=open_time + timedelta(seconds=event_offset_seconds),
        received_at=open_time + timedelta(seconds=received_offset_seconds),
        open_time=open_time,
        close_time=open_time + timedelta(seconds=close_offset_seconds),
        open=Decimal("100"),
        high=Decimal(str(101 + minute)),
        low=Decimal("99"),
        close=Decimal(str(100 + minute)),
        base_volume=Decimal("10"),
        closed=closed,
    )


def build(
    candles: tuple[Candle, ...],
    *,
    as_of: datetime,
    cutoff: datetime,
    quality: str = "healthy",
):
    return build_evidence_snapshot(
        candles,
        symbol="BTCUSDT",
        as_of=as_of,
        knowledge_cutoff=cutoff,
        quality=quality,
        quality_reasons=() if quality == "healthy" else ("test_quality_failure",),
        collector_session_id=SESSION_ID,
        watermark_digest="a" * 64,
        required_intervals=("1m",),
    )


def candles(count: int = 21) -> tuple[Candle, ...]:
    return tuple(candle(index) for index in range(count))


def test_builder_uses_only_closed_candles_inside_both_inclusive_time_boundaries() -> None:
    as_of = BASE + timedelta(minutes=21)
    cutoff = as_of + timedelta(seconds=10)
    inputs = (
        *candles(),
        candle(21, closed=False, identity="unfinished"),
        candle(21, event_offset_seconds=61, identity="event-after"),
        candle(21, close_offset_seconds=61, identity="close-after"),
        candle(21, received_offset_seconds=71, identity="received-after"),
    )

    snapshot = build(inputs, as_of=as_of, cutoff=cutoff)

    assert tuple(item.normalized_event_id for item in snapshot.candles) == (
        *(source_id("normalized", index) for index in range(21)),
    )
    assert all(item.closed for item in snapshot.candles)


def test_builder_is_deterministic_and_prior_snapshot_is_immutable_under_late_arrival() -> None:
    as_of = BASE + timedelta(minutes=21)
    cutoff = as_of + timedelta(seconds=10)
    original = candles()
    first = build(original, as_of=as_of, cutoff=cutoff)

    late = candle(20, received_offset_seconds=180, identity="late")
    same_cutoff = build((*original, late), as_of=as_of, cutoff=cutoff)

    assert same_cutoff == first
    assert build(tuple(reversed(original)), as_of=as_of, cutoff=cutoff) == first
    assert first.evidence_id == first.evidence_digest
    assert len(first.evidence_digest) == 64
    assert len(first.input_digest) == 64
    assert first.quality == "healthy"
    assert first.collector_session_id == SESSION_ID
    assert first.watermark_digest == "a" * 64
    assert all(item.raw_event_id and item.raw_payload_hash for item in first.items)
    assert tuple(item.normalized_event_id for item in first.candles) == (
        *(source_id("normalized", index) for index in range(21)),
    )


def test_pti_004_uses_only_explicit_clocks_and_never_reads_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChangedWallClock(datetime):
        current = datetime(2000, 1, 1, tzinfo=UTC)
        reads = 0

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            cls.reads += 1
            return cls.current if tz is None else cls.current.astimezone(tz)

        @classmethod
        def utcnow(cls) -> datetime:
            cls.reads += 1
            return cls.current.replace(tzinfo=None)

        @classmethod
        def today(cls) -> datetime:
            cls.reads += 1
            return cls.current

    monkeypatch.setattr(builder_module, "datetime", ChangedWallClock)
    inputs = candles()
    as_of = BASE + timedelta(minutes=21)
    cutoff = as_of + timedelta(seconds=10)

    ChangedWallClock.current = datetime(2000, 1, 1, tzinfo=UTC)
    first = build(inputs, as_of=as_of, cutoff=cutoff)
    ChangedWallClock.current = datetime(2099, 12, 31, tzinfo=UTC)
    second = build(inputs, as_of=as_of, cutoff=cutoff)

    assert ChangedWallClock.reads == 0
    assert second == first


def test_builder_fails_closed_when_an_interval_has_no_complete_window() -> None:
    with pytest.raises(EvidenceBuildError, match="1m"):
        build(
            candles(20),
            as_of=BASE + timedelta(minutes=21),
            cutoff=BASE + timedelta(minutes=22),
        )


def test_builder_rejects_a_terminal_window_that_is_stale_at_the_cutoffs() -> None:
    with pytest.raises(EvidenceBuildError, match="terminal candle is stale"):
        build(
            candles(),
            as_of=BASE + timedelta(days=3),
            cutoff=BASE + timedelta(days=3),
        )


def test_pti_003_missing_raw_id_cannot_enter_evidence() -> None:
    with pytest.raises(ValueError, match="raw_event_id"):
        replace(candle(0), raw_event_id="")


def test_pti_003_stale_collector_fails_closed() -> None:
    with pytest.raises(EvidenceBuildError, match="collector session"):
        _validate_source_authority(
            {
                "session_id": SESSION_ID,
                "stream": "btcusdt@kline_1m",
                "last_sequence": 7,
                "observed_at": BASE.isoformat(),
            },
            collector_session_id=SESSION_ID,
            collector_status="stale",
            raw_stream="btcusdt@kline_1m",
            normalized_stream="btcusdt@kline_1m",
            normalized_sequence=7,
            received_at=BASE,
        )


@pytest.mark.parametrize("interval", ["1m", "5m", "1h", "4h"])
def test_pti_003_watermark_binds_each_approved_kline_stream(interval: str) -> None:
    _validate_source_authority(
        {
            "session_id": SESSION_ID,
            "stream": f"btcusdt@kline_{interval}",
            "last_sequence": 7,
            "observed_at": BASE.isoformat(),
        },
        collector_session_id=SESSION_ID,
        collector_status="healthy",
        raw_stream=f"btcusdt@kline_{interval}",
        normalized_stream=f"btcusdt@kline_{interval}",
        normalized_sequence=7,
        received_at=BASE,
    )


def test_pti_003_raw_stream_mismatch_fails_closed() -> None:
    with pytest.raises(EvidenceBuildError, match="raw stream"):
        _validate_source_authority(
            {
                "session_id": SESSION_ID,
                "stream": "btcusdt@kline_1m",
                "last_sequence": 7,
                "observed_at": BASE.isoformat(),
            },
            collector_session_id=SESSION_ID,
            collector_status="healthy",
            raw_stream="ethusdt@kline_1m",
            normalized_stream="btcusdt@kline_1m",
            normalized_sequence=7,
            received_at=BASE,
        )


def test_pti_003_incomplete_watermark_fails_closed() -> None:
    complete = {
        "session_id": SESSION_ID,
        "stream": "btcusdt@kline_1m",
        "last_sequence": 7,
        "observed_at": BASE.isoformat(),
    }
    invalid_watermarks = (
        {key: value for key, value in complete.items() if key != "session_id"},
        {**complete, "session_id": "018f7000-0000-7000-8000-000000000999"},
        {**complete, "stream": ""},
        {**complete, "stream": "ethusdt@kline_1m"},
        {**complete, "last_sequence": 6},
        {**complete, "observed_at": "not-a-time"},
        {**complete, "observed_at": (BASE + timedelta(seconds=1)).isoformat()},
        {**complete, "unexpected": "field"},
    )
    for watermark in invalid_watermarks:
        with pytest.raises(EvidenceBuildError, match="watermark"):
            _validate_source_authority(
                watermark,
                collector_session_id=SESSION_ID,
                collector_status="healthy",
                raw_stream="btcusdt@kline_1m",
                normalized_stream="btcusdt@kline_1m",
                normalized_sequence=7,
                received_at=BASE,
            )


@pytest.mark.parametrize("quality", ["degraded", "stale", "invalid", "reconnecting"])
def test_builder_rejects_every_non_healthy_quality(quality: str) -> None:
    with pytest.raises(EvidenceBuildError, match="healthy quality"):
        build(
            candles(),
            as_of=BASE + timedelta(minutes=21),
            cutoff=BASE + timedelta(minutes=22),
            quality=quality,
        )


def test_newer_cutoff_deterministically_replaces_a_same_bucket_late_materialization() -> None:
    as_of = BASE + timedelta(minutes=21)
    old_cutoff = BASE + timedelta(minutes=21, seconds=10)
    original = candles()
    replacement = candle(20, received_offset_seconds=120, identity="replacement")

    old_snapshot = build(original, as_of=as_of, cutoff=old_cutoff)
    unchanged = build((*original, replacement), as_of=as_of, cutoff=old_cutoff)
    newer = build(
        (*original, replacement),
        as_of=as_of,
        cutoff=BASE + timedelta(minutes=22),
    )

    assert unchanged == old_snapshot
    assert source_id("normalized", "replacement") not in {
        item.normalized_event_id for item in old_snapshot.candles
    }
    assert source_id("normalized", "replacement") in {
        item.normalized_event_id for item in newer.candles
    }
    assert newer.evidence_digest != old_snapshot.evidence_digest
