from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib

import pytest

from evidence_worker.features import FeatureDerivationError, derive_interval_features
from evidence_worker.types import Candle


BASE = datetime(2026, 7, 19, tzinfo=UTC)


def source_id(kind: str, identity: object) -> str:
    return hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()


def candle(
    minute: int,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    open_price: str = "100",
) -> Candle:
    open_time = BASE + timedelta(minutes=minute)
    return Candle(
        normalized_event_id=source_id("normalized", minute),
        raw_event_id=source_id("raw", minute),
        raw_payload_hash=f"{minute:064x}",
        symbol="BTCUSDT",
        interval="1m",
        event_time=open_time + timedelta(seconds=59),
        received_at=open_time + timedelta(seconds=59, milliseconds=100),
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59),
        open=Decimal(open_price),
        high=Decimal(high or close),
        low=Decimal(low or open_price),
        close=Decimal(close),
        base_volume=Decimal("10"),
        closed=True,
    )


def test_derives_approved_decimal_features_with_ordered_provenance() -> None:
    candles = tuple(
        candle(
            index,
            str(100 + index),
            high=str(101 + index),
            low=str(99 + index),
            open_price=str(100 + index),
        )
        for index in range(21)
    )

    observations = derive_interval_features(candles)

    assert tuple(item.name for item in observations) == (
        "close_return_1",
        "close_sma_20",
        "close_rsi_14",
    )
    assert tuple(item.value_text for item in observations) == (
        "0.008403361344537815",
        "110.500000000000000000",
        "100.000000000000000000",
    )
    assert observations[0].input_normalized_event_ids == (
        source_id("normalized", 19),
        source_id("normalized", 20),
    )
    assert observations[1].input_raw_event_ids == tuple(
        source_id("raw", index) for index in range(1, 21)
    )
    assert observations[2].input_raw_event_ids == tuple(
        source_id("raw", index) for index in range(21)
    )
    assert all(isinstance(item.value, Decimal) for item in observations)
    assert all(len(item.input_digest) == 64 for item in observations)


def test_feature_digest_is_independent_of_caller_order() -> None:
    ordered = tuple(candle(index, str(100 + index)) for index in range(21))

    assert derive_interval_features(ordered) == derive_interval_features(tuple(reversed(ordered)))


def test_feature_derivation_rejects_incomplete_or_gapped_windows() -> None:
    with pytest.raises(FeatureDerivationError, match="21 contiguous"):
        derive_interval_features(tuple(candle(index, "100") for index in range(20)))
    with pytest.raises(FeatureDerivationError, match="contiguous"):
        derive_interval_features(tuple(candle(index, "100") for index in (*range(20), 21)))


@pytest.mark.parametrize(
    ("closes", "expected_rsi"),
    [
        (["100"] * 21, "50.000000000000000000"),
        ([str(120 - index) for index in range(21)], "0.000000000000000000"),
    ],
)
def test_wilder_rsi_has_deterministic_flat_and_all_loss_edges(
    closes: list[str], expected_rsi: str
) -> None:
    observations = derive_interval_features(
        tuple(candle(index, close) for index, close in enumerate(closes))
    )

    assert observations[2].name == "close_rsi_14"
    assert observations[2].value_text == expected_rsi
