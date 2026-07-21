from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from market_data_worker.normalization import make_raw_event
from market_data_worker.pipeline import (
    CollectorPipeline,
    InMemoryMarketStore,
    QualityPersistenceError,
)
from market_data_worker.types import QualityStatus


def test_kline_updates_with_same_last_trade_id_have_distinct_source_identity() -> None:
    received = datetime(2026, 7, 19, tzinfo=UTC)
    base = {
        "e": "kline",
        "E": 1784419200100,
        "s": "BTCUSDT",
        "k": {
            "t": 1784419200000,
            "T": 1784419259999,
            "s": "BTCUSDT",
            "i": "1m",
            "f": 100,
            "L": 101,
            "o": "60000",
            "c": "60000.1",
            "h": "60001",
            "l": "59999",
            "v": "1",
            "n": 2,
            "x": False,
            "q": "60000",
            "V": "0.5",
            "Q": "30000",
            "B": "0",
        },
    }
    updated = json.loads(json.dumps(base))
    updated["E"] = 1784419200200
    updated["k"]["c"] = "60000.2"
    closed = json.loads(json.dumps(updated))
    closed["E"] = 1784419260000
    closed["k"]["x"] = True

    raw_base = make_raw_event(
        "018f7000-0000-7000-8000-000000000001", "btcusdt@kline_1m", base, received
    )
    raw_updated = make_raw_event(
        "018f7000-0000-7000-8000-000000000001", "btcusdt@kline_1m", updated, received
    )
    raw_closed = make_raw_event(
        "018f7000-0000-7000-8000-000000000001", "btcusdt@kline_1m", closed, received
    )

    assert (
        len(
            {
                raw_base.source_dedupe_key,
                raw_updated.source_dedupe_key,
                raw_closed.source_dedupe_key,
            }
        )
        == 3
    )


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


def test_quality_append_failure_is_invalid_and_propagates_to_stop_the_collector() -> None:
    class FailingQualityStore(InMemoryMarketStore):
        def append_quality(self, event: object) -> None:
            raise OSError("injected quality persistence failure")

    pipeline = CollectorPipeline(FailingQualityStore())

    with pytest.raises(QualityPersistenceError, match="collector continuation is unsafe"):
        pipeline.ingest(SESSION_ID, "btcusdt@trade", {"invalid": True}, received_at())

    assert pipeline.state.status is QualityStatus.INVALID
    assert pipeline.state.reasons == ["quality_append_failed"]
