from __future__ import annotations

from datetime import UTC, datetime

from market_data_worker.pipeline import CollectorPipeline, InMemoryMarketStore
from market_data_worker.types import QualityStatus


SESSION_ID = "018f7000-0000-7000-8000-000000000001"


def received_at() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


def test_raw_append_precedes_normalization_and_decimal_strings_are_preserved() -> None:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)
    payload = {
        "e": "trade",
        "E": 1784419200000,
        "s": "BTCUSDT",
        "t": 100,
        "p": "60000.10000000",
        "q": "0.01000000",
        "T": 1784419200000,
        "m": False,
        "M": True,
    }

    result = pipeline.ingest(SESSION_ID, "btcusdt@trade", payload, received_at())

    assert result.accepted is True
    assert result.normalized is not None
    assert result.normalized.payload["price"] == "60000.10000000"
    assert result.normalized.payload["quantity"] == "0.01000000"
    assert store.operations == ["append_raw", "append_normalized"]
    assert result.normalized.raw_event_id == store.raw_events[0].raw_event_id
    assert pipeline.state.status is QualityStatus.DEGRADED
    assert "stream_incomplete" in pipeline.state.reasons


def test_duplicate_and_out_of_order_inputs_have_no_second_normalized_effect() -> None:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)
    first = {
        "e": "trade",
        "E": 1784419200000,
        "s": "BTCUSDT",
        "t": 100,
        "p": "60000.1",
        "q": "0.01",
        "T": 1784419200000,
        "m": False,
        "M": True,
    }
    out_of_order = {**first, "t": 99, "E": 1784419199999, "T": 1784419199999}

    assert pipeline.ingest(SESSION_ID, "btcusdt@trade", first, received_at()).accepted
    duplicate = pipeline.ingest(SESSION_ID, "btcusdt@trade", first, received_at())
    rejected = pipeline.ingest(SESSION_ID, "btcusdt@trade", out_of_order, received_at())

    assert duplicate.accepted is False and duplicate.reason == "duplicate"
    assert rejected.accepted is False and rejected.reason == "out_of_order"
    assert len(store.normalized_events) == 1
    assert pipeline.state.status is QualityStatus.DEGRADED


def test_same_source_key_with_different_payload_is_invalid_not_a_duplicate() -> None:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)
    first = {
        "e": "trade",
        "E": 1784419200000,
        "s": "BTCUSDT",
        "t": 100,
        "p": "60000.1",
        "q": "0.01",
        "T": 1784419200000,
        "m": False,
        "M": True,
    }

    assert pipeline.ingest(SESSION_ID, "btcusdt@trade", first, received_at()).accepted
    conflict = pipeline.ingest(
        SESSION_ID,
        "btcusdt@trade",
        {**first, "p": "60001.1"},
        received_at(),
    )

    assert conflict.accepted is False and conflict.reason == "dedupe_conflict"
    assert len(store.normalized_events) == 1
    assert pipeline.state.status is QualityStatus.INVALID


def test_unresolved_gap_cannot_be_healed_by_an_unrelated_stream() -> None:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)
    first = {
        "e": "trade",
        "E": 1784419200000,
        "s": "BTCUSDT",
        "t": 100,
        "p": "60000.1",
        "q": "0.01",
        "T": 1784419200000,
        "m": False,
        "M": True,
    }
    book = {
        "u": 500,
        "s": "ETHUSDT",
        "b": "3000.1",
        "B": "1.0",
        "a": "3000.2",
        "A": "1.1",
    }

    assert pipeline.ingest(SESSION_ID, "btcusdt@trade", first, received_at()).accepted
    assert (
        pipeline.ingest(
            SESSION_ID,
            "btcusdt@trade",
            {**first, "t": 103, "E": 1784419200003, "T": 1784419200003},
            received_at(),
        ).reason
        == "sequence_gap"
    )
    assert pipeline.ingest(SESSION_ID, "ethusdt@bookTicker", book, received_at()).accepted

    assert pipeline.state.status is QualityStatus.STALE
    assert "sequence_gap" in pipeline.state.reasons
