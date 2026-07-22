from __future__ import annotations

from datetime import UTC, datetime, timedelta

from market_data_worker.capabilities import fixed_phase_two_streams
from market_data_worker.pipeline import CollectorPipeline, InMemoryMarketStore
from market_data_worker.types import QualityStatus


NOW = datetime(2026, 7, 19, tzinfo=UTC)
SESSION = "018f7000-0000-7000-8000-000000000001"


def payload_for(stream: str, sequence: int) -> dict[str, object]:
    symbol_text, kind = stream.split("@", 1)
    symbol = symbol_text.upper()
    if kind == "trade":
        return {
            "e": "trade",
            "E": 1784419200000 + sequence,
            "s": symbol,
            "t": sequence,
            "p": "60000.1",
            "q": "0.01",
            "T": 1784419200000 + sequence,
            "m": False,
            "M": True,
        }
    if kind == "bookTicker":
        return {"u": sequence, "s": symbol, "b": "60000.1", "B": "1", "a": "60000.2", "A": "2"}
    interval = kind.removeprefix("kline_")
    return {
        "e": "kline",
        "E": 1784419200000 + sequence,
        "s": symbol,
        "k": {
            "t": 1784419200000,
            "T": 1784419259999,
            "s": symbol,
            "i": interval,
            "f": sequence,
            "L": sequence,
            "o": "60000",
            "c": "60000.1",
            "h": "60001",
            "l": "59999",
            "v": "10",
            "n": 1,
            "x": False,
            "q": "600000",
            "V": "5",
            "Q": "300000",
            "B": "0",
        },
    }


def test_symbol_is_healthy_only_after_all_six_expected_streams_are_fresh() -> None:
    pipeline = CollectorPipeline(InMemoryMarketStore())
    streams = fixed_phase_two_streams()

    pipeline.ingest(SESSION, streams[0].name, payload_for(streams[0].name, 100), NOW)
    assert pipeline.symbol_quality("BTCUSDT")[0] is QualityStatus.DEGRADED
    assert "stream_incomplete" in pipeline.symbol_quality("BTCUSDT")[1]

    for index, stream in enumerate(streams[1:6], start=101):
        assert pipeline.ingest(SESSION, stream.name, payload_for(stream.name, index), NOW).accepted
    assert pipeline.symbol_quality("BTCUSDT")[0] is QualityStatus.HEALTHY
    assert pipeline.state.status is QualityStatus.DEGRADED

    for index, stream in enumerate(streams[6:], start=201):
        assert pipeline.ingest(SESSION, stream.name, payload_for(stream.name, index), NOW).accepted
    assert pipeline.state.status is QualityStatus.HEALTHY

    pipeline.evaluate_freshness(NOW + timedelta(seconds=91))
    assert pipeline.symbol_quality("BTCUSDT")[0] is QualityStatus.STALE
    assert pipeline.symbol_quality("ETHUSDT")[0] is QualityStatus.STALE


def test_disconnect_downgrades_every_expected_stream_and_both_symbols() -> None:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)

    pipeline.mark_global_failure(QualityStatus.RECONNECTING, "socket_disconnected", NOW)

    assert pipeline.symbol_quality("BTCUSDT")[0] is QualityStatus.RECONNECTING
    assert pipeline.symbol_quality("ETHUSDT")[0] is QualityStatus.RECONNECTING
    assert len(store.quality_events) == 12


def test_closed_kline_interval_grid_gap_is_rejected() -> None:
    pipeline = CollectorPipeline(InMemoryMarketStore())
    stream = "btcusdt@kline_1m"
    first = payload_for(stream, 100)
    first["k"]["x"] = True  # type: ignore[index]
    second = payload_for(stream, 101)
    second["k"]["t"] = 1784419320000  # type: ignore[index]
    second["k"]["T"] = 1784419379999  # type: ignore[index]
    second["k"]["x"] = True  # type: ignore[index]

    assert pipeline.ingest(SESSION, stream, first, NOW).accepted
    result = pipeline.ingest(SESSION, stream, second, NOW + timedelta(minutes=2))

    assert result.accepted is False and result.reason == "kline_gap"
    assert pipeline.state.status is QualityStatus.STALE
