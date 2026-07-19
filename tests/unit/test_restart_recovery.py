from __future__ import annotations

from datetime import UTC, datetime

from market_data_worker.pipeline import CollectorPipeline, InMemoryMarketStore
from market_data_worker.recovery import ContinuitySnapshot, RestartCoordinator
from market_data_worker.types import NormalizedMarketEvent, QualityStatus


NOW = datetime(2026, 7, 19, tzinfo=UTC)
SESSION = "018f7000-0000-7000-8000-000000000001"
PAYLOAD = {
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


class CrashAfterRawStore(InMemoryMarketStore):
    def __init__(self) -> None:
        super().__init__()
        self.crash = True

    def append_normalized(self, event: NormalizedMarketEvent) -> None:
        if self.crash:
            self.crash = False
            raise OSError("injected crash after durable raw append")
        super().append_normalized(event)


class Repository:
    def __init__(self, store: CrashAfterRawStore) -> None:
        self.store = store

    def load(self) -> ContinuitySnapshot:
        return ContinuitySnapshot(
            session_id=SESSION,
            watermarks={},
            last_sequences={},
            stream_statuses={},
            pending_raw=tuple(self.store.raw_events),
        )


def test_restart_bootstraps_durable_state_and_recovers_pending_raw_once() -> None:
    store = CrashAfterRawStore()
    first = CollectorPipeline(store)
    assert first.ingest(SESSION, "btcusdt@trade", PAYLOAD, NOW).reason == "normalized_append_failed"
    assert len(store.raw_events) == 1 and len(store.normalized_events) == 0

    restarted = CollectorPipeline(store)
    recovered = RestartCoordinator(Repository(store)).restore(restarted)
    repeated = RestartCoordinator(Repository(store)).restore(restarted)

    assert recovered == 1 and repeated == 0
    assert len(store.normalized_events) == 1
    assert restarted.last_sequence("trade", "BTCUSDT") == 100
    assert restarted.symbol_quality("BTCUSDT")[0] is QualityStatus.DEGRADED
