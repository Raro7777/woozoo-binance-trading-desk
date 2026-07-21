from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

import pytest
import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from control_api.security import (
    ACTOR_ID,
    CommandGuardRejected,
    LocalOperatorSecurity,
    PostgresSecurityRepository,
    SessionRecord,
    SessionRejected,
    make_password_verifier,
    token_digest,
)
from control_api.trading_room import PostgresTradingRoom, TradingRoomError, canonical_hash
from paper_engine.authorization_worker import PostgresWorkerStateReporter
from paper_engine.persistence import PostgresPaperStore
from risk_engine import KillActivation, PostgresKillSwitch
from docker_infrastructure_lock import docker_infrastructure_lock


ROOT = Path(__file__).parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
CONTROL_URL = "postgresql://woozoo_control_api@127.0.0.1:5433/woozoo"
PAPER_URL = "postgresql://woozoo_paper_engine@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
OPENAPI = json.loads((ROOT / "packages/contracts/spec/openapi.v1.json").read_text("utf-8"))


def validate_openapi_response(schema_name: str, value: object) -> None:
    uri = "https://schemas.woozoo.local/openapi/v1"
    registry = Registry().with_resource(
        uri, Resource.from_contents(OPENAPI, default_specification=DRAFT202012)
    )
    Draft202012Validator(
        {"$ref": f"{uri}#/components/schemas/{schema_name}"},
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(value)


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def test_postgres_trading_room_reloads_genesis_and_kill_across_instances() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            reporter = PostgresWorkerStateReporter(PAPER_URL, instance_id="postgres-test-worker")
            reporter.start()
            reporter.heartbeat(None)
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT status,ready,last_progress_at FROM paper_authorization_worker_reader_v1"
                ).fetchone() == ("HEALTHY", True, None)
            first = PostgresTradingRoom(CONTROL_URL)
            second = PostgresTradingRoom(CONTROL_URL)

            initial_portfolio = first.portfolio()
            validate_openapi_response("PaperPortfolioViewV1", initial_portfolio)
            validate_openapi_response("TradingRoomDashboardV1", first.dashboard())
            assert initial_portfolio["available_quote"] == "10000.000000000000000000"
            assert initial_portfolio["reconciliation_health"] == "FAILED"
            assert initial_portfolio["ledger_health"] == "HEALTHY"
            assert second.portfolio() == first.portfolio()
            with psycopg.connect(DATABASE_URL) as connection:
                source_health = connection.execute(
                    "SELECT reconciliation_status,ledger_status,ledger_imbalance_count "
                    "FROM paper_health_reader_v1 WHERE account_id=%s",
                    ("c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e",),
                ).fetchone()
                assert source_health == ("MISSING", "BALANCED", 0)
                connection.execute(
                    "INSERT INTO paper_reconciliation_checkpoints"
                    "(checkpoint_id,account_id,input_digest,output_digest,status,mismatch_codes,"
                    "created_at) VALUES (%s,%s,%s,%s,'FAILED','[\"TEST_MISMATCH\"]'::jsonb,%s)",
                    (
                        "f" * 64,
                        "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e",
                        "1" * 64,
                        "2" * 64,
                        datetime(2026, 7, 20, 0, 30, tzinfo=UTC),
                    ),
                )
            assert first.portfolio()["reconciliation_health"] == "FAILED"
            with pytest.raises(TradingRoomError) as blocked:
                first.create_analysis("BTCUSDT", "must-not-fall-back-to-memory")
            assert blocked.value.code == "PRODUCTION_AUTHORITY_UNAVAILABLE"
            assert blocked.value.status_code == 503

            activation = PostgresKillSwitch(DATABASE_URL).activate(
                KillActivation(
                    request_id="trading-room-restart-authority",
                    expected_version=0,
                    trigger_kind="MANUAL",
                    actor_id="operator:postgres-authority-test",
                    reason_code="MANUAL_SAFETY_STOP",
                    reason="verify every control-api instance reads the durable Kill barrier",
                    observed_at=datetime(2026, 7, 20, 1, 0, tzinfo=UTC),
                    context_digest=canonical_hash({"test": "restart-authority"}),
                )
            )
            assert activation.version == 1
            with psycopg.connect(DATABASE_URL) as connection:
                activation_payload_hash = connection.execute(
                    "SELECT payload_hash FROM outbox_events WHERE event_id=%s",
                    (activation.outbox_event_id,),
                ).fetchone()[0]
            paper_store = PostgresPaperStore(PAPER_URL)
            completion = paper_store.consume_kill_activation(
                activation.activation_event_id,
                activation_payload_hash,
                received_at=datetime.now(UTC),
            )
            assert completion.completion_created is True
            with psycopg.connect(DATABASE_URL) as connection:
                completion_state_digest = connection.execute(
                    "SELECT state_digest FROM paper_kill_cancel_completions "
                    "WHERE activation_event_id=%s",
                    (activation.activation_event_id,),
                ).fetchone()[0]
            checkpoint = paper_store.reconcile(
                "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e",
                checkpoint_id="phase7-direct-recovery-authority",
                created_at=datetime.now(UTC),
            )
            assert checkpoint.input_digest == completion_state_digest
            reporter.stop()
            recovery_arguments = (
                "phase7-direct-recovery-command",
                "1" * 64,
                activation.activation_event_id,
                "INCIDENT-P7-DIRECT",
                "verify mutation authority rejects unavailable worker and market books",
                "2" * 64,
                "3" * 64,
                "4" * 64,
                datetime.now(UTC),
            )
            with pytest.raises(psycopg.errors.RaiseException, match="WORKER_NOT_READY"):
                with psycopg.connect(DATABASE_URL) as connection:
                    connection.execute(
                        "SELECT created,response FROM recover_kill_switch_v1("
                        "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
                        recovery_arguments,
                    )
            reporter.start()
            reporter.heartbeat(None)
            with pytest.raises(psycopg.errors.RaiseException, match="DATA_UNHEALTHY"):
                with psycopg.connect(DATABASE_URL) as connection:
                    connection.execute(
                        "SELECT created,response FROM recover_kill_switch_v1("
                        "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
                        recovery_arguments,
                    )
            assert first.kill_switch()["active"] is True
            validate_openapi_response("KillSwitchViewV1", first.kill_switch())
            assert second.kill_switch() == first.kill_switch()
            assert PostgresTradingRoom(CONTROL_URL).kill_switch() == first.kill_switch()
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute("SET session_replication_role='replica'")
                connection.execute(
                    "INSERT INTO paper_ledger_transactions"
                    "(transaction_id,account_id,business_event_type,business_event_id,"
                    "journal_kind,posted_at) VALUES (%s,%s,'test.authority-corruption.v1',"
                    "%s,'PHYSICAL',%s)",
                    (
                        "e" * 64,
                        "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e",
                        "ledger-authority-test",
                        datetime(2026, 7, 20, 1, 1, tzinfo=UTC),
                    ),
                )
                connection.execute(
                    "INSERT INTO paper_ledger_entries"
                    "(transaction_id,line_no,account_code,commodity,debit,credit) "
                    "VALUES (%s,0,'TEST:IMBALANCE','USDT',1,0)",
                    ("e" * 64,),
                )
            assert first.portfolio()["ledger_health"] == "FAILED"
        finally:
            run("docker", "compose", "down", "-v")


def test_stale_concurrent_session_touch_cannot_resurrect_logged_out_session() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            repository = PostgresSecurityRepository(
                CONTROL_URL, make_password_verifier("session-race-password")
            )
            issued_at = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
            original = SessionRecord(
                digest="a" * 64,
                actor_id=ACTOR_ID,
                issued_at=issued_at,
                last_seen_at=issued_at,
                idle_expires_at=datetime(2026, 7, 20, 5, 30, tzinfo=UTC),
                absolute_expires_at=datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
            )
            repository.rotate_session(original, None, issued_at)
            stale_touch = SessionRecord(
                digest=original.digest,
                actor_id=original.actor_id,
                issued_at=original.issued_at,
                last_seen_at=datetime(2026, 7, 20, 5, 1, tzinfo=UTC),
                idle_expires_at=datetime(2026, 7, 20, 5, 31, tzinfo=UTC),
                absolute_expires_at=original.absolute_expires_at,
            )
            logged_out = Event()
            stale_rejected: list[bool] = []

            def logout() -> None:
                repository.save_session(
                    SessionRecord(
                        digest=original.digest,
                        actor_id=original.actor_id,
                        issued_at=original.issued_at,
                        last_seen_at=original.last_seen_at,
                        idle_expires_at=original.idle_expires_at,
                        absolute_expires_at=original.absolute_expires_at,
                        revoked_at=datetime(2026, 7, 20, 5, 0, 30, tzinfo=UTC),
                    )
                )
                logged_out.set()

            def touch_from_stale_read() -> None:
                logged_out.wait(timeout=5)
                try:
                    repository.save_session(stale_touch)
                except SessionRejected:
                    stale_rejected.append(True)

            logout_thread = Thread(target=logout)
            stale_thread = Thread(target=touch_from_stale_read)
            stale_thread.start()
            logout_thread.start()
            logout_thread.join(timeout=5)
            stale_thread.join(timeout=5)

            assert stale_rejected == [True]
            persisted = repository.get_session(original.digest)
            assert persisted is not None
            assert persisted.revoked_at == datetime(2026, 7, 20, 5, 0, 30, tzinfo=UTC)
            assert persisted.last_seen_at == original.last_seen_at
        finally:
            run("docker", "compose", "down", "-v")


def test_postgres_command_guard_and_logout_serialize_and_roll_back() -> None:
    class PausingRepository(PostgresSecurityRepository):
        def __init__(self, database_url: str, password_verifier: str) -> None:
            super().__init__(database_url, password_verifier)
            self.pause_effect: str | None = None
            self.session_locked = Event()
            self.release_session = Event()
            self.fail_before_session_update = False

        def pause_next(self, effect: str) -> None:
            self.pause_effect = effect
            self.session_locked.clear()
            self.release_session.clear()

        def _after_command_session_locked(self, *, revoke_session: bool) -> None:
            effect = "logout" if revoke_session else "command"
            if self.pause_effect == effect:
                self.session_locked.set()
                if not self.release_session.wait(timeout=5):
                    raise TimeoutError("session row lock was not released")
                self.pause_effect = None

        def _before_command_session_update(self, *, revoke_session: bool) -> None:
            del revoke_session
            if self.fail_before_session_update:
                raise RuntimeError("INJECTED_SESSION_UPDATE_FAILURE")

    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            verifier = make_password_verifier("postgres-command-race-password")
            repository = PausingRepository(CONTROL_URL, verifier)
            clock = [datetime(2026, 7, 20, 6, 0, tzinfo=UTC)]
            security = LocalOperatorSecurity(
                verifier,
                "https://localhost:3443",
                repository=repository,
                clock=lambda: clock[0],
            )
            raw_session = security.login(
                "postgres-command-race-password",
                "https://localhost:3443",
            )
            command_csrf = security.issue_csrf(raw_session)
            logout_csrf = security.issue_csrf(raw_session)
            command_done = Event()
            logout_done = Event()
            errors: list[type[Exception]] = []

            def command() -> None:
                try:
                    security.consume_command_guard(
                        raw_session,
                        command_csrf,
                        "https://localhost:3443",
                    )
                except Exception as error:  # noqa: BLE001 - thread result is asserted below
                    errors.append(type(error))
                finally:
                    command_done.set()

            def logout() -> None:
                try:
                    security.logout(raw_session, logout_csrf, "https://localhost:3443")
                except Exception as error:  # noqa: BLE001 - thread result is asserted below
                    errors.append(type(error))
                finally:
                    logout_done.set()

            repository.pause_next("command")
            command_thread = Thread(target=command)
            command_thread.start()
            assert repository.session_locked.wait(timeout=5)
            logout_thread = Thread(target=logout)
            logout_thread.start()
            try:
                assert logout_done.wait(timeout=0.2) is False
            finally:
                repository.release_session.set()
                command_thread.join(timeout=5)
                logout_thread.join(timeout=5)

            assert not command_thread.is_alive() and not logout_thread.is_alive()
            assert command_done.is_set() and logout_done.is_set()
            assert errors == []
            with psycopg.connect(DATABASE_URL) as connection:
                session_row = connection.execute(
                    "SELECT revoked_at FROM operator_sessions WHERE session_digest=%s",
                    (token_digest(raw_session),),
                ).fetchone()
                csrf_rows = dict(
                    connection.execute(
                        "SELECT csrf_token_digest,consumed_at FROM session_csrf_tokens "
                        "WHERE csrf_token_digest IN (%s,%s)",
                        (token_digest(command_csrf), token_digest(logout_csrf)),
                    ).fetchall()
                )
            assert session_row is not None and session_row[0] == clock[0]
            assert csrf_rows[token_digest(logout_csrf)] == clock[0]
            assert csrf_rows[token_digest(command_csrf)] == clock[0]

            raw_session = security.login(
                "postgres-command-race-password",
                "https://localhost:3443",
            )
            command_csrf = security.issue_csrf(raw_session)
            logout_csrf = security.issue_csrf(raw_session)
            command_done.clear()
            logout_done.clear()
            errors.clear()
            repository.pause_next("logout")
            logout_thread = Thread(target=logout)
            logout_thread.start()
            assert repository.session_locked.wait(timeout=5)
            command_thread = Thread(target=command)
            command_thread.start()
            try:
                assert command_done.wait(timeout=0.2) is False
            finally:
                repository.release_session.set()
                logout_thread.join(timeout=5)
                command_thread.join(timeout=5)

            assert not command_thread.is_alive() and not logout_thread.is_alive()
            assert logout_done.is_set() and command_done.is_set()
            assert errors == [SessionRejected]
            with psycopg.connect(DATABASE_URL) as connection:
                session_row = connection.execute(
                    "SELECT revoked_at FROM operator_sessions WHERE session_digest=%s",
                    (token_digest(raw_session),),
                ).fetchone()
                csrf_rows = dict(
                    connection.execute(
                        "SELECT csrf_token_digest,consumed_at FROM session_csrf_tokens "
                        "WHERE csrf_token_digest IN (%s,%s)",
                        (token_digest(command_csrf), token_digest(logout_csrf)),
                    ).fetchall()
                )
            assert session_row is not None and session_row[0] == clock[0]
            assert csrf_rows[token_digest(logout_csrf)] == clock[0]
            assert csrf_rows[token_digest(command_csrf)] is None

            raw_session = security.login(
                "postgres-command-race-password",
                "https://localhost:3443",
            )
            rollback_csrf = security.issue_csrf(raw_session)
            session_digest = token_digest(raw_session)
            with psycopg.connect(DATABASE_URL) as connection:
                before_failure = connection.execute(
                    "SELECT last_seen_at,idle_expires_at,revoked_at FROM operator_sessions "
                    "WHERE session_digest=%s",
                    (session_digest,),
                ).fetchone()
            clock[0] += timedelta(minutes=1)
            repository.fail_before_session_update = True
            with pytest.raises(RuntimeError, match="INJECTED_SESSION_UPDATE_FAILURE"):
                security.consume_command_guard(
                    raw_session,
                    rollback_csrf,
                    "https://localhost:3443",
                )
            repository.fail_before_session_update = False
            with psycopg.connect(DATABASE_URL) as connection:
                assert (
                    connection.execute(
                        "SELECT last_seen_at,idle_expires_at,revoked_at FROM operator_sessions "
                        "WHERE session_digest=%s",
                        (session_digest,),
                    ).fetchone()
                    == before_failure
                )
                assert connection.execute(
                    "SELECT consumed_at FROM session_csrf_tokens WHERE csrf_token_digest=%s",
                    (token_digest(rollback_csrf),),
                ).fetchone() == (None,)

            with pytest.raises(CommandGuardRejected):
                security.consume_command_guard(
                    raw_session,
                    "unknown-csrf",
                    "https://localhost:3443",
                )
            with psycopg.connect(DATABASE_URL) as connection:
                assert (
                    connection.execute(
                        "SELECT last_seen_at,idle_expires_at,revoked_at FROM operator_sessions "
                        "WHERE session_digest=%s",
                        (session_digest,),
                    ).fetchone()
                    == before_failure
                )

            security.consume_command_guard(
                raw_session,
                rollback_csrf,
                "https://localhost:3443",
            )
            with psycopg.connect(DATABASE_URL) as connection:
                after_success = connection.execute(
                    "SELECT last_seen_at,idle_expires_at,revoked_at FROM operator_sessions "
                    "WHERE session_digest=%s",
                    (session_digest,),
                ).fetchone()
            clock[0] += timedelta(minutes=1)
            with pytest.raises(CommandGuardRejected):
                security.consume_command_guard(
                    raw_session,
                    rollback_csrf,
                    "https://localhost:3443",
                )
            with psycopg.connect(DATABASE_URL) as connection:
                assert (
                    connection.execute(
                        "SELECT last_seen_at,idle_expires_at,revoked_at FROM operator_sessions "
                        "WHERE session_digest=%s",
                        (session_digest,),
                    ).fetchone()
                    == after_success
                )
        finally:
            run("docker", "compose", "down", "-v")
