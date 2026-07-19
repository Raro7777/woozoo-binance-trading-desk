"""Production-coupled public market-data supervision with bounded ingress."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import json
from itertools import count
import random
from typing import Callable, Protocol
from uuid import uuid4

from platform_core.config import PlatformSettings

from .capabilities import PublicStream, fixed_phase_two_streams
from .failures import ReconnectPolicy
from .persistence import PostgresMarketStore
from .pipeline import CollectorPipeline
from .queueing import BackpressureOverflow, BoundedIngressQueue
from .recovery import PostgresRestartRepository, RestartCoordinator
from .settings import MarketDataSettings, MarketDataSource
from .transport import PublicWebSocketTransport
from .types import QualityStatus


MAX_CONNECTION_AGE = timedelta(hours=23, minutes=45)


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class WebSocketTransport(Protocol):
    def messages(self, streams: tuple[PublicStream, ...]) -> AsyncIterator[bytes]: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class MarketDataSupervisor:
    def __init__(
        self,
        pipeline: CollectorPipeline,
        transport: WebSocketTransport,
        clock: Clock,
        session_ids: Iterator[str],
        reconnect_policy: ReconnectPolicy,
        session_starter: Callable[[str, str, datetime], None] | None = None,
        jitter_seed: int = 0,
    ) -> None:
        self._pipeline = pipeline
        self._transport = transport
        self._clock = clock
        self._session_ids = session_ids
        self._policy = reconnect_policy
        self._session_starter = session_starter
        self._jitter = random.Random(jitter_seed)
        self._streams = fixed_phase_two_streams()
        self._allowed_streams = {stream.name for stream in self._streams}

    @classmethod
    def live(
        cls,
        environment: Mapping[str, str],
        *,
        pipeline: CollectorPipeline | None = None,
        transport: WebSocketTransport | None = None,
        clock: Clock | None = None,
        session_ids: Iterator[str] | None = None,
        reconnect_policy: ReconnectPolicy | None = None,
        jitter_seed: int | None = None,
    ) -> "MarketDataSupervisor":
        PlatformSettings.from_mapping(environment)
        settings = MarketDataSettings.from_mapping(environment)
        if settings.source is not MarketDataSource.SPOT_PUBLIC:
            raise ValueError("live supervisor requires MARKET_DATA_SOURCE=spot_public")
        database_url = environment.get("MARKET_DATABASE_URL")
        if not database_url:
            raise ValueError("MARKET_DATABASE_URL is required for the market writer")
        configured_seed = environment.get("MARKET_RECONNECT_JITTER_SEED", "0")
        try:
            resolved_seed = int(configured_seed) if jitter_seed is None else jitter_seed
        except ValueError as error:
            raise ValueError("MARKET_RECONNECT_JITTER_SEED must be an integer") from error
        active_pipeline = pipeline
        session_starter: Callable[[str, str, datetime], None] | None = None
        if active_pipeline is None:
            store = PostgresMarketStore(database_url)
            active_pipeline = CollectorPipeline(store)
            session_starter = store.create_session
            try:
                RestartCoordinator(PostgresRestartRepository(database_url)).restore(active_pipeline)
            except RuntimeError as error:
                if str(error) != "restart continuity requires a durable collector session":
                    raise
        return cls(
            active_pipeline,
            transport or PublicWebSocketTransport(),
            clock or SystemClock(),
            session_ids or (str(uuid4()) for _ in count()),
            reconnect_policy or ReconnectPolicy(),
            session_starter,
            resolved_seed,
        )

    async def run(self, *, max_generations: int | None = None) -> CollectorPipeline:
        attempt = 0
        generations = 0
        while max_generations is None or generations < max_generations:
            session_id = next(self._session_ids)
            if self._session_starter is not None:
                self._session_starter(session_id, f"public-stream-{session_id}", self._clock.now())
            self._pipeline.confirm_generation(session_id)
            outcome = await self._run_generation(session_id)
            generations += 1
            if outcome == "overflow":
                break
            if outcome == "planned_rotation":
                self._pipeline.mark_global_failure(
                    QualityStatus.RECONNECTING,
                    "planned_connection_rotation",
                    self._clock.now(),
                )
                attempt = 0
                await self._clock.sleep(0)
                continue
            if outcome == "server_shutdown":
                self._pipeline.mark_global_failure(
                    QualityStatus.RECONNECTING, "server_shutdown", self._clock.now()
                )
                attempt += 1
                cooldown = self._policy.cooldown_after_attempt(attempt)
                if cooldown is not None:
                    self._pipeline.mark_global_failure(
                        QualityStatus.RECONNECTING,
                        "reconnect_cooldown",
                        self._clock.now(),
                    )
                    await self._clock.sleep(cooldown.total_seconds())
                    attempt = 0
                else:
                    await self._clock.sleep(
                        self._reconnect_delay(
                            max(0, attempt - 1),
                            immediate=attempt == 1,
                        )
                    )
                continue
            attempt += 1
            self._pipeline.mark_global_failure(
                QualityStatus.STALE, "socket_disconnected", self._clock.now()
            )
            cooldown = self._policy.cooldown_after_attempt(attempt)
            if cooldown is not None:
                self._pipeline.mark_global_failure(
                    QualityStatus.RECONNECTING, "reconnect_cooldown", self._clock.now()
                )
                await self._clock.sleep(cooldown.total_seconds())
                attempt = 0
            else:
                await self._clock.sleep(self._reconnect_delay(max(1, attempt - 1)))
        return self._pipeline

    def _reconnect_delay(self, attempt: int, *, immediate: bool = False) -> float:
        if immediate:
            return self._policy.delay_seconds(attempt, immediate=True)
        jitter = self._jitter.uniform(-0.25, 0.25)
        return self._policy.delay_seconds(attempt, jitter=jitter)

    async def _run_generation(self, session_id: str) -> str:
        connected_at = self._clock.now()
        queue = BoundedIngressQueue[bytes](10_000)
        finished = asyncio.Event()
        outcome = ["completed"]

        async def produce() -> None:
            try:
                async for message in self._transport.messages(self._streams):
                    try:
                        queue.offer(message)
                    except BackpressureOverflow:
                        outcome[0] = "overflow"
                        self._pipeline.mark_global_failure(
                            QualityStatus.INVALID, "queue_overflow", self._clock.now()
                        )
                        return
            except Exception:
                outcome[0] = "disconnect"
            finally:
                finished.set()

        producer = asyncio.create_task(produce())
        try:
            while not finished.is_set() or queue.size:
                if self._clock.now() - connected_at >= MAX_CONNECTION_AGE:
                    return "planned_rotation"
                self._pipeline.evaluate_freshness(self._clock.now())
                if outcome[0] == "overflow":
                    return "overflow"
                if queue.size == 0:
                    await asyncio.sleep(0.01)
                    continue
                message = queue.take()
                result = self._ingest_message(session_id, message)
                if result == "server_shutdown":
                    return result
            return outcome[0]
        finally:
            if not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer

    def _ingest_message(self, session_id: str, message: bytes) -> str:
        stream = "invalid"
        try:
            envelope = json.loads(message)
            if isinstance(envelope, dict):
                candidate = envelope.get("stream")
                data = envelope.get("data")
                raw_shutdown = (
                    set(envelope) == {"e", "E"}
                    and envelope.get("e") == "serverShutdown"
                    and isinstance(envelope.get("E"), int)
                    and not isinstance(envelope.get("E"), bool)
                )
                combined_shutdown = (
                    set(envelope) == {"stream", "data"}
                    and candidate == "!serverShutdown"
                    and isinstance(data, dict)
                    and set(data) == {"e", "E"}
                    and data.get("e") == "serverShutdown"
                    and isinstance(data.get("E"), int)
                    and not isinstance(data.get("E"), bool)
                )
                if raw_shutdown or combined_shutdown:
                    stream = "!serverShutdown"
                elif isinstance(candidate, str) and candidate in self._allowed_streams:
                    stream = candidate
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        result = self._pipeline.ingest(session_id, stream, message, self._clock.now())
        return result.reason
