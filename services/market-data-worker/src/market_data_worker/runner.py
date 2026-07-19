"""Authoritative Phase 2 worker entry point for recorded or live-public input."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from platform_core.config import PlatformSettings

from .failures import ReconnectPolicy
from .pipeline import CollectorPipeline
from .replay import ReplayResult, load_recorded_events, replay_recorded_events
from .settings import MarketDataSettings, MarketDataSource
from .supervisor import Clock, MarketDataSupervisor, WebSocketTransport


async def run_market_data(
    environment: Mapping[str, str],
    *,
    recorded_path: Path | None = None,
    pipeline: CollectorPipeline | None = None,
    transport: WebSocketTransport | None = None,
    clock: Clock | None = None,
    session_ids: Iterator[str] | None = None,
    reconnect_policy: ReconnectPolicy | None = None,
    max_generations: int | None = None,
) -> CollectorPipeline | ReplayResult:
    """Dispatch the closed recorded/live modes without a second transport path."""

    PlatformSettings.from_mapping(environment)
    source = MarketDataSettings.from_mapping(environment).source
    if source is MarketDataSource.RECORDED:
        if recorded_path is None:
            raise ValueError("recorded mode requires an explicit recorded input path")
        return replay_recorded_events(load_recorded_events(recorded_path))

    supervisor = MarketDataSupervisor.live(
        environment,
        pipeline=pipeline,
        transport=transport,
        clock=clock,
        session_ids=session_ids,
        reconnect_policy=reconnect_policy,
    )
    return await supervisor.run(max_generations=max_generations)


async def collect_public_generation(
    environment: dict[str, str],
    *,
    transport: WebSocketTransport | None = None,
) -> CollectorPipeline:
    """Compatibility wrapper that still uses the authoritative bounded supervisor."""

    result = await run_market_data(
        environment,
        transport=transport,
        max_generations=1,
    )
    if not isinstance(result, CollectorPipeline):
        raise TypeError("public generation must return a collector pipeline")
    return result
