"""Dedicated Phase 7 Paper authorization worker runtime.

This process has no inbound network listener. It polls the durable pending
authorization projection and delegates every financial effect to the existing
transactional Paper authority.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import os
import signal
from threading import Event
from time import monotonic
from typing import Protocol
from uuid import uuid4

import psycopg

from .persistence import (
    PAPER_DEFAULT_ACCOUNT_ID,
    CommitResult,
    Phase7AuthorizationWorker,
    PostgresPaperStore,
)


@dataclass(frozen=True, slots=True)
class AuthorizationWorkerSettings:
    database_url: str
    poll_interval_seconds: float
    reconciliation_interval_seconds: float

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> AuthorizationWorkerSettings:
        if environment.get("TRADING_MODE") != "paper":
            raise ValueError("PAPER_AUTHORIZATION_WORKER_REQUIRES_TRADING_MODE_PAPER")
        database_url = environment.get("PAPER_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("PAPER_DATABASE_URL_REQUIRED")
        raw_interval = environment.get("PAPER_AUTHORIZATION_POLL_INTERVAL_MS", "250")
        try:
            interval_ms = int(raw_interval)
        except ValueError as exc:
            raise ValueError("PAPER_AUTHORIZATION_POLL_INTERVAL_INVALID") from exc
        if not 50 <= interval_ms <= 60_000:
            raise ValueError("PAPER_AUTHORIZATION_POLL_INTERVAL_INVALID")
        raw_reconciliation_interval = environment.get("PAPER_RECONCILIATION_INTERVAL_MS", "5000")
        try:
            reconciliation_interval_ms = int(raw_reconciliation_interval)
        except ValueError as exc:
            raise ValueError("PAPER_RECONCILIATION_INTERVAL_INVALID") from exc
        if not 250 <= reconciliation_interval_ms <= 60_000:
            raise ValueError("PAPER_RECONCILIATION_INTERVAL_INVALID")
        return cls(
            database_url,
            interval_ms / 1000,
            reconciliation_interval_ms / 1000,
        )


class AuthorizationAttemptRunner(Protocol):
    def run_once(self) -> object | None: ...


@dataclass(frozen=True, slots=True)
class KillActivationProgress:
    activation_event_id: str
    cancelled_count: int
    completion_created: bool


class DurableKillActivationWorker:
    """Consume the durable Risk activation projection before new orders."""

    def __init__(self, store: PostgresPaperStore) -> None:
        self._store = store

    def run_once(self) -> KillActivationProgress | None:
        with psycopg.connect(self._store.database_url) as connection:
            pending = connection.execute(
                "SELECT activation_event_id,payload_hash "
                "FROM paper_pending_kill_activations_v1 "
                "ORDER BY ingestion_sequence LIMIT 1"
            ).fetchone()
        if pending is None:
            return None
        cancelled_count = 0
        completion_created = False
        while True:
            consumed = self._store.consume_kill_activation(
                pending[0], pending[1], received_at=datetime.now(UTC)
            )
            cancelled_count += consumed.cancelled_count
            completion_created = completion_created or consumed.completion_created
            if not consumed.has_more:
                return KillActivationProgress(pending[0], cancelled_count, completion_created)


class ReconcilingAuthorizationRunner:
    """Consume authorizations and keep health fresh after all Paper effects.

    The periodic checkpoint also observes Kill cancellation effects, which do
    not pass through the authorization consumer.
    """

    def __init__(
        self,
        store: PostgresPaperStore,
        *,
        reconciliation_interval_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._store = store
        self._kill_worker = DurableKillActivationWorker(store)
        self._worker = Phase7AuthorizationWorker(store)
        self._interval = reconciliation_interval_seconds
        self._clock = clock
        self._last_reconciliation: float | None = None

    def run_once(self) -> object | None:
        result: object | None = self._kill_worker.run_once()
        if result is None:
            result = self._worker.run_once()
        now = self._clock()
        due = self._last_reconciliation is None or now - self._last_reconciliation >= self._interval
        if result is not None or due:
            reconciliation = self._store.reconcile(
                PAPER_DEFAULT_ACCOUNT_ID,
                checkpoint_id=f"paper-runtime-{uuid4()}",
                created_at=datetime.now(UTC),
            )
            if reconciliation.status != "HEALTHY":
                raise RuntimeError(
                    "PAPER_RECONCILIATION_FAILED:" + ",".join(reconciliation.mismatch_codes)
                )
            self._last_reconciliation = now
        return result


class WorkerStateReporter(Protocol):
    def start(self) -> None: ...

    def heartbeat(self, result: object | None) -> None: ...

    def fail(self, error_code: str) -> None: ...

    def stop(self) -> None: ...


class NullWorkerStateReporter:
    def start(self) -> None:
        return None

    def heartbeat(self, result: object | None) -> None:
        del result

    def fail(self, error_code: str) -> None:
        del error_code

    def stop(self) -> None:
        return None


class PostgresWorkerStateReporter:
    worker_name = "phase7-paper-authorization"

    def __init__(self, database_url: str, *, instance_id: str | None = None) -> None:
        self._database_url = database_url
        self._instance_id = instance_id or str(uuid4())

    def start(self) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "INSERT INTO paper_authorization_worker_state"
                "(worker_name,instance_id,status,started_at,heartbeat_at,last_progress_at,"
                "last_result,last_error_code,stopped_at) "
                "VALUES (%s,%s,'RUNNING',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,NULL,NULL,NULL,NULL) "
                "ON CONFLICT (worker_name) DO UPDATE SET instance_id=EXCLUDED.instance_id,"
                "status='RUNNING',started_at=EXCLUDED.started_at,"
                "heartbeat_at=EXCLUDED.heartbeat_at,last_progress_at=NULL,last_result=NULL,"
                "last_error_code=NULL,stopped_at=NULL",
                (self.worker_name, self._instance_id),
            )

    @staticmethod
    def _result_label(result: object | None) -> str | None:
        if isinstance(result, KillActivationProgress):
            return "KILL_ACTIVATION_CONSUMED"
        if isinstance(result, CommitResult):
            value = result.response.get("result")
            return value if isinstance(value, str) else "AUTHORIZATION_ATTEMPTED"
        return None

    def heartbeat(self, result: object | None) -> None:
        label = self._result_label(result)
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "UPDATE paper_authorization_worker_state SET heartbeat_at=CURRENT_TIMESTAMP,"
                "last_progress_at=CASE WHEN %s::varchar IS NULL THEN last_progress_at "
                "ELSE CURRENT_TIMESTAMP END,last_result=COALESCE(%s::varchar,last_result) "
                "WHERE worker_name=%s AND instance_id=%s AND status='RUNNING'",
                (label, label, self.worker_name, self._instance_id),
            )

    def fail(self, error_code: str) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "UPDATE paper_authorization_worker_state SET status='FAILED',"
                "heartbeat_at=CURRENT_TIMESTAMP,last_error_code=%s,"
                "stopped_at=CURRENT_TIMESTAMP WHERE worker_name=%s AND instance_id=%s",
                (error_code[:128], self.worker_name, self._instance_id),
            )

    def stop(self) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "UPDATE paper_authorization_worker_state SET status='STOPPED',"
                "heartbeat_at=CURRENT_TIMESTAMP,stopped_at=CURRENT_TIMESTAMP "
                "WHERE worker_name=%s AND instance_id=%s AND status='RUNNING'",
                (self.worker_name, self._instance_id),
            )


class AuthorizationWorkerRuntime:
    def __init__(
        self,
        runner: AuthorizationAttemptRunner,
        *,
        poll_interval_seconds: float,
        state: WorkerStateReporter | None = None,
    ) -> None:
        self._runner = runner
        self._poll_interval = poll_interval_seconds
        self._state = state or NullWorkerStateReporter()

    def serve(self, stop: Event) -> None:
        failures = 0
        self._state.start()
        try:
            while not stop.is_set():
                try:
                    result = self._runner.run_once()
                except psycopg.Error:
                    failures += 1
                    delay = min(self._poll_interval * (2 ** min(failures, 5)), 5.0)
                except Exception as exc:
                    code = str(exc).split(":", 1)[0] or type(exc).__name__
                    self._state.fail(code)
                    raise
                else:
                    failures = 0
                    self._state.heartbeat(result)
                    delay = 0.0 if result is not None else self._poll_interval
                stop.wait(delay)
        finally:
            if stop.is_set():
                self._state.stop()


def _install_signal_handlers(stop: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def build_runtime(
    environment: Mapping[str, str],
) -> tuple[AuthorizationWorkerSettings, AuthorizationWorkerRuntime]:
    settings = AuthorizationWorkerSettings.from_environment(environment)
    runner = ReconcilingAuthorizationRunner(
        PostgresPaperStore(settings.database_url),
        reconciliation_interval_seconds=settings.reconciliation_interval_seconds,
    )
    return settings, AuthorizationWorkerRuntime(
        runner,
        poll_interval_seconds=settings.poll_interval_seconds,
        state=PostgresWorkerStateReporter(settings.database_url),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    _settings, runtime = build_runtime(os.environ)
    if args.check_config:
        return 0
    stop = Event()
    _install_signal_handlers(stop)
    runtime.serve(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
