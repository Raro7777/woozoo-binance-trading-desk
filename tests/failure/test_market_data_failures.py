from __future__ import annotations

from datetime import UTC, datetime, timedelta

from market_data_worker.failures import RateLimitGuard, ReconnectPolicy
from market_data_worker.pipeline import CollectorPipeline, InMemoryMarketStore
from market_data_worker.types import QualityStatus


SESSION_ID = "018f7000-0000-7000-8000-000000000001"
NOW = datetime(2026, 7, 19, tzinfo=UTC)


class FailingRawStore(InMemoryMarketStore):
    def append_raw(self, event: object) -> bool:
        raise OSError("injected durable append failure")


def test_data_003_shutdown_is_control_only_and_reconnect_is_bounded() -> None:
    pipeline = CollectorPipeline(InMemoryMarketStore())
    result = pipeline.ingest(
        SESSION_ID,
        "!serverShutdown",
        {"e": "serverShutdown", "E": 1784419200000},
        NOW,
    )
    policy = ReconnectPolicy()

    assert result.accepted is False and result.reason == "server_shutdown"
    assert result.normalized is None
    assert pipeline.state.status is QualityStatus.RECONNECTING
    assert policy.delay_seconds(0, immediate=True) == 0
    assert policy.delay_seconds(1, jitter=0) == 1
    assert policy.delay_seconds(10, jitter=0) == 30
    assert policy.cooldown_after_attempt(10) == timedelta(minutes=5)
    assert policy.cooldown_after_attempt(11) == timedelta(minutes=5)


def test_data_004_429_honors_retry_after_and_prevents_followup_call() -> None:
    guard = RateLimitGuard()
    guard.observe(429, {"Retry-After": "7"}, NOW)

    assert guard.may_call(NOW + timedelta(seconds=6)) is False
    assert guard.may_call(NOW + timedelta(seconds=7)) is True
    assert guard.observed_418 is False

    guard.observe(418, {"Retry-After": "30"}, NOW)
    assert guard.observed_418 is True
    assert guard.may_call(NOW + timedelta(seconds=29)) is False
    assert guard.may_call(NOW + timedelta(seconds=30)) is True


def test_data_005_malformed_payload_is_quarantined_and_invalid() -> None:
    store = InMemoryMarketStore()
    pipeline = CollectorPipeline(store)
    result = pipeline.ingest(
        SESSION_ID,
        "btcusdt@trade",
        {"e": "trade", "s": "BTCUSDT", "t": 1},
        NOW,
    )

    assert result.accepted is False and result.reason == "schema_invalid"
    assert len(store.raw_events) == 1
    assert len(store.normalized_events) == 0
    assert pipeline.state.status is QualityStatus.INVALID


def test_data_006_raw_append_failure_has_no_normalized_effect() -> None:
    store = FailingRawStore()
    pipeline = CollectorPipeline(store)
    result = pipeline.ingest(
        SESSION_ID,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": 1784419200000,
            "s": "BTCUSDT",
            "t": 1,
            "p": "60000.1",
            "q": "0.01",
            "T": 1784419200000,
            "m": False,
            "M": True,
        },
        NOW,
    )

    assert result.accepted is False and result.reason == "raw_append_failed"
    assert store.normalized_events == []
    assert pipeline.state.status is QualityStatus.INVALID
    assert store.quality_events[-1].raw_event_id is None
