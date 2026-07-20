from threading import Event

import psycopg
import pytest

from paper_engine.authorization_worker import (
    AuthorizationWorkerRuntime,
    AuthorizationWorkerSettings,
    DurableKillActivationWorker,
    KillActivationProgress,
    ReconcilingAuthorizationRunner,
)
from paper_engine.persistence import CommitResult, KillCancelResult, ReconciliationResult


def test_worker_settings_fail_closed_without_explicit_paper_mode_and_database() -> None:
    with pytest.raises(ValueError, match="TRADING_MODE_PAPER"):
        AuthorizationWorkerSettings.from_environment({"PAPER_DATABASE_URL": "postgresql://paper"})
    with pytest.raises(ValueError, match="PAPER_DATABASE_URL_REQUIRED"):
        AuthorizationWorkerSettings.from_environment({"TRADING_MODE": "paper"})
    with pytest.raises(ValueError, match="POLL_INTERVAL_INVALID"):
        AuthorizationWorkerSettings.from_environment(
            {
                "TRADING_MODE": "paper",
                "PAPER_DATABASE_URL": "postgresql://paper",
                "PAPER_AUTHORIZATION_POLL_INTERVAL_MS": "0",
            }
        )


def test_worker_polling_survives_transient_database_failure_and_processes_next_item() -> None:
    class Runner:
        attempts = 0

        def run_once(self) -> CommitResult | None:
            self.attempts += 1
            if self.attempts == 1:
                raise psycopg.OperationalError("database restarting")
            if self.attempts == 2:
                return CommitResult(True, {"result": "CONSUMED_ORDER_CREATED"}, "a" * 64)
            stop.set()
            return None

    stop = Event()
    runner = Runner()

    class State:
        events: list[object] = []

        def start(self) -> None:
            self.events.append("start")

        def heartbeat(self, result: object | None) -> None:
            self.events.append(result)

        def fail(self, error_code: str) -> None:
            self.events.append(("failed", error_code))

        def stop(self) -> None:
            self.events.append("stop")

    state = State()
    AuthorizationWorkerRuntime(runner, poll_interval_seconds=0, state=state).serve(stop)

    assert runner.attempts == 3
    assert state.events[0] == "start"
    assert state.events[-1] == "stop"
    assert not any(isinstance(item, tuple) and item[0] == "failed" for item in state.events)


def test_worker_records_startup_and_post_effect_reconciliation() -> None:
    class Store:
        checkpoints = 0

        def reconcile(
            self, _account_id: str, *, checkpoint_id: str, created_at: object
        ) -> ReconciliationResult:
            assert checkpoint_id.startswith("paper-runtime-")
            assert created_at is not None
            self.checkpoints += 1
            return ReconciliationResult("HEALTHY", (), "a" * 64, "b" * 64)

    class Attempts:
        results = [None, CommitResult(True, {"result": "CONSUMED_ORDER_CREATED"}, "c" * 64)]

        def run_once(self) -> CommitResult | None:
            return self.results.pop(0)

    store = Store()
    runner = ReconcilingAuthorizationRunner(
        store,  # type: ignore[arg-type]
        reconciliation_interval_seconds=60,
        clock=lambda: 1.0,
    )
    runner._kill_worker = Attempts()  # type: ignore[assignment]
    runner._kill_worker.results = [None, None]  # type: ignore[attr-defined]
    runner._worker = Attempts()  # type: ignore[assignment]

    assert runner.run_once() is None
    assert runner.run_once() is not None
    assert store.checkpoints == 2


def test_pending_kill_activation_is_completed_before_authorization_and_replay_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cursor:
        def __init__(self, row: tuple[str, str] | None) -> None:
            self._row = row

        def fetchone(self) -> tuple[str, str] | None:
            return self._row

    class Connection:
        def __init__(self, row: tuple[str, str] | None) -> None:
            self._row = row

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str) -> Cursor:
            assert "paper_pending_kill_activations_v1" in query
            return Cursor(self._row)

    pending = [("a" * 64, "b" * 64), None]
    monkeypatch.setattr(
        "paper_engine.authorization_worker.psycopg.connect",
        lambda _url: Connection(pending.pop(0)),
    )

    class Store:
        database_url = "postgresql://paper"
        calls = 0

        def consume_kill_activation(
            self, activation_event_id: str, payload_hash: str, *, received_at: object
        ) -> KillCancelResult:
            assert activation_event_id == "a" * 64
            assert payload_hash == "b" * 64
            assert received_at is not None
            self.calls += 1
            return KillCancelResult(
                self.calls == 1,
                self.calls == 1,
                activation_event_id,
                "c" * 64 if self.calls == 1 else None,
                "d" * 64,
                100 if self.calls == 1 else 1,
                self.calls == 1,
                self.calls == 2,
            )

    store = Store()
    worker = DurableKillActivationWorker(store)  # type: ignore[arg-type]
    result = worker.run_once()

    assert result == KillActivationProgress("a" * 64, 101, True)
    assert store.calls == 2
    assert worker.run_once() is None


def test_supervised_worker_failure_is_recorded_before_exit() -> None:
    class Runner:
        def run_once(self) -> None:
            raise RuntimeError("PAPER_RECONCILIATION_FAILED:LEDGER_MISMATCH")

    class State:
        failed: str | None = None

        def start(self) -> None:
            return None

        def heartbeat(self, result: object | None) -> None:
            del result

        def fail(self, error_code: str) -> None:
            self.failed = error_code

        def stop(self) -> None:
            return None

    state = State()
    with pytest.raises(RuntimeError, match="PAPER_RECONCILIATION_FAILED"):
        AuthorizationWorkerRuntime(Runner(), poll_interval_seconds=0, state=state).serve(Event())
    assert state.failed == "PAPER_RECONCILIATION_FAILED"
