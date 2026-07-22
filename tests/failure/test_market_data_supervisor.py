from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
import random

import pytest

from market_data_worker.failures import ReconnectPolicy
from market_data_worker.pipeline import (
    CollectorPipeline,
    InMemoryMarketStore,
    QualityPersistenceError,
)
from market_data_worker.runner import run_market_data
from market_data_worker.supervisor import MarketDataSupervisor
from market_data_worker.types import QualityEvent, QualityStatus


NOW = datetime(2026, 7, 19, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.current = NOW
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class Transport:
    def __init__(self, generations: list[list[bytes] | Exception]) -> None:
        self.generations = generations
        self.calls = 0

    async def messages(self, _streams: object) -> AsyncIterator[bytes]:
        generation = self.generations[self.calls]
        self.calls += 1
        if isinstance(generation, Exception):
            raise generation
        for message in generation:
            yield message


class AdvancingTransport:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls = 0

    async def messages(self, _streams: object) -> AsyncIterator[bytes]:
        self.calls += 1
        self.clock.current += timedelta(hours=23, minutes=45)
        yield b'{"bad":true}'


class FailOnQueueOverflowStore(InMemoryMarketStore):
    def append_quality(self, event: QualityEvent) -> None:
        if event.reason == "queue_overflow":
            raise OSError("injected queue-overflow quality persistence failure")
        super().append_quality(event)


def test_connection_is_planned_rotated_at_23_hours_45_minutes() -> None:
    clock = Clock()
    transport = AdvancingTransport(clock)
    pipeline = CollectorPipeline(InMemoryMarketStore())
    supervisor = MarketDataSupervisor.live(
        {
            "TRADING_MODE": "paper",
            "MARKET_DATA_SOURCE": "spot_public",
            "MARKET_DATABASE_URL": "postgresql://writer",
        },
        pipeline=pipeline,
        transport=transport,
        clock=clock,
        session_ids=iter(("s1", "s2")),
    )

    asyncio.run(supervisor.run(max_generations=2))

    assert transport.calls == 2
    assert clock.sleeps[0] == 0
    assert "planned_connection_rotation" in pipeline.state.reasons


def test_seeded_jitter_is_deterministic_and_applied_to_reconnect_backoff() -> None:
    seed = 1729
    clock = Clock()
    policy = ReconnectPolicy()
    supervisor = MarketDataSupervisor.live(
        {
            "TRADING_MODE": "paper",
            "MARKET_DATA_SOURCE": "spot_public",
            "MARKET_DATABASE_URL": "postgresql://writer",
        },
        pipeline=CollectorPipeline(InMemoryMarketStore()),
        transport=Transport([OSError("one"), OSError("two")]),
        clock=clock,
        session_ids=iter(("s1", "s2")),
        reconnect_policy=policy,
        jitter_seed=seed,
    )

    asyncio.run(supervisor.run(max_generations=2))

    expected_random = random.Random(seed)
    expected = [
        policy.delay_seconds(1, jitter=expected_random.uniform(-0.25, 0.25)),
        policy.delay_seconds(1, jitter=expected_random.uniform(-0.25, 0.25)),
    ]
    assert clock.sleeps == pytest.approx(expected)
    assert clock.sleeps != [1.0, 1.0]


def test_live_supervisor_reconnects_immediately_for_shutdown_then_backs_off() -> None:
    shutdown = b'{"stream":"!serverShutdown","data":{"e":"serverShutdown","E":1784419200000}}'
    transport = Transport([[shutdown], OSError("disconnect"), []])
    clock = Clock()
    pipeline = CollectorPipeline(InMemoryMarketStore())
    result = asyncio.run(
        run_market_data(
            {
                "TRADING_MODE": "paper",
                "MARKET_DATA_SOURCE": "spot_public",
                "MARKET_DATABASE_URL": "postgresql://writer",
            },
            pipeline=pipeline,
            transport=transport,
            clock=clock,
            session_ids=iter(("s1", "s2", "s3")),
            max_generations=3,
        )
    )

    assert result is pipeline
    assert transport.calls == 3
    assert clock.sleeps[0] == 0
    expected_jitter = random.Random(0).uniform(-0.25, 0.25)
    assert clock.sleeps[1] == pytest.approx(
        ReconnectPolicy().delay_seconds(1, jitter=expected_jitter)
    )
    assert pipeline.symbol_quality("BTCUSDT")[0] is not QualityStatus.HEALTHY
    assert pipeline.symbol_quality("ETHUSDT")[0] is not QualityStatus.HEALTHY


def test_live_supervisor_accepts_the_raw_shutdown_control_form() -> None:
    shutdown = b'{"e":"serverShutdown","E":1784419200000}'
    transport = Transport([[shutdown], []])
    clock = Clock()
    result = asyncio.run(
        run_market_data(
            {
                "TRADING_MODE": "paper",
                "MARKET_DATA_SOURCE": "spot_public",
                "MARKET_DATABASE_URL": "postgresql://writer",
            },
            pipeline=CollectorPipeline(InMemoryMarketStore()),
            transport=transport,
            clock=clock,
            session_ids=iter(("s1", "s2")),
            max_generations=2,
        )
    )

    assert isinstance(result, CollectorPipeline)
    assert transport.calls == 2
    assert clock.sleeps[0] == 0


def test_repeated_shutdowns_consume_the_reconnect_budget_and_cool_down() -> None:
    shutdown = b'{"e":"serverShutdown","E":1784419200000}'
    transport = Transport([[shutdown] for _ in range(11)])
    clock = Clock()
    result = asyncio.run(
        run_market_data(
            {
                "TRADING_MODE": "paper",
                "MARKET_DATA_SOURCE": "spot_public",
                "MARKET_DATABASE_URL": "postgresql://writer",
            },
            pipeline=CollectorPipeline(InMemoryMarketStore()),
            transport=transport,
            clock=clock,
            session_ids=iter(f"s{index}" for index in range(11)),
            max_generations=11,
        )
    )

    assert isinstance(result, CollectorPipeline)
    assert transport.calls == 11
    assert 300 in clock.sleeps


def test_shutdown_control_rejects_a_market_stream_wrapper() -> None:
    wrong_stream = b'{"stream":"btcusdt@trade","data":{"e":"serverShutdown","E":1784419200000}}'
    pipeline = CollectorPipeline(InMemoryMarketStore())
    result = asyncio.run(
        run_market_data(
            {
                "TRADING_MODE": "paper",
                "MARKET_DATA_SOURCE": "spot_public",
                "MARKET_DATABASE_URL": "postgresql://writer",
            },
            pipeline=pipeline,
            transport=Transport([[wrong_stream]]),
            clock=Clock(),
            session_ids=iter(("s1",)),
            max_generations=1,
        )
    )

    assert result is pipeline
    assert pipeline.state.status is QualityStatus.INVALID
    assert "server_shutdown" not in pipeline.state.reasons


def test_live_supervisor_uses_bounded_queue_and_persists_overflow_failure() -> None:
    messages = [b'{"bad":true}'] * 10_001
    pipeline = CollectorPipeline(InMemoryMarketStore())
    result = asyncio.run(
        run_market_data(
            {
                "TRADING_MODE": "paper",
                "MARKET_DATA_SOURCE": "spot_public",
                "MARKET_DATABASE_URL": "postgresql://writer",
            },
            pipeline=pipeline,
            transport=Transport([messages]),
            clock=Clock(),
            session_ids=iter(("s1",)),
            max_generations=1,
        )
    )

    assert result is pipeline
    assert pipeline.state.status is QualityStatus.INVALID
    assert "queue_overflow" in pipeline.state.reasons


def test_quality_persistence_failure_is_fatal_before_queue_drain_or_reconnect() -> None:
    message = (
        b'{"stream":"btcusdt@bookTicker","data":'
        b'{"u":1,"s":"BTCUSDT","b":"60000.1","B":"1",'
        b'"a":"60000.2","A":"2"}}'
    )
    store = FailOnQueueOverflowStore()
    pipeline = CollectorPipeline(store)
    transport = Transport([[message] * 10_001, [message]])
    issued_sessions: list[str] = []

    def session_ids() -> Iterator[str]:
        for session_id in ("s1", "s2"):
            issued_sessions.append(session_id)
            yield session_id

    with pytest.raises(QualityPersistenceError, match="collector continuation is unsafe"):
        asyncio.run(
            run_market_data(
                {
                    "TRADING_MODE": "paper",
                    "MARKET_DATA_SOURCE": "spot_public",
                    "MARKET_DATABASE_URL": "postgresql://writer",
                },
                pipeline=pipeline,
                transport=transport,
                clock=Clock(),
                session_ids=session_ids(),
                max_generations=2,
            )
        )

    assert store.normalized_events == []
    assert pipeline.state.status is QualityStatus.INVALID
    assert pipeline.state.reasons == ["quality_append_failed"]
    assert transport.calls == 1
    assert issued_sessions == ["s1"]
