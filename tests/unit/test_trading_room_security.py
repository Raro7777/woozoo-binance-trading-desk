from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest

from control_api.security import (
    ACTOR_ID,
    CommandGuardRejected,
    InMemorySecurityRepository,
    LocalOperatorSecurity,
    SessionRejected,
    make_password_verifier,
    token_digest,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def service(clock: Clock) -> LocalOperatorSecurity:
    return LocalOperatorSecurity(
        make_password_verifier("correct horse battery staple"),
        "https://localhost:3443",
        clock=clock,
    )


def test_auth_001_session_rotation_csrf_one_time_and_absolute_expiry() -> None:
    clock = Clock()
    auth = service(clock)
    first = auth.login("correct horse battery staple", "https://localhost:3443")
    token = auth.issue_csrf(first)

    session = auth.consume_command_guard(first, token, "https://localhost:3443")
    assert session.actor_id == ACTOR_ID
    with pytest.raises(CommandGuardRejected):
        auth.consume_command_guard(first, token, "https://localhost:3443")

    second = auth.login(
        "correct horse battery staple", "https://localhost:3443", prior_raw_session=first
    )
    with pytest.raises(SessionRejected):
        auth.authenticate(first)
    assert auth.authenticate(second).actor_id == ACTOR_ID

    clock.value += timedelta(hours=8, seconds=1)
    with pytest.raises(SessionRejected):
        auth.authenticate(second)


def test_auth_002_foreign_or_missing_origin_has_effect_zero() -> None:
    clock = Clock()
    auth = service(clock)
    raw_session = auth.login("correct horse battery staple", "https://localhost:3443")
    token = auth.issue_csrf(raw_session)

    for origin in (None, "null", "http://localhost:3443", "https://evil.invalid"):
        with pytest.raises(CommandGuardRejected):
            auth.consume_command_guard(raw_session, token, origin)

    assert (
        auth.consume_command_guard(raw_session, token, "https://localhost:3443").actor_id
        == ACTOR_ID
    )


def test_logout_revokes_session_and_consumes_csrf() -> None:
    clock = Clock()
    auth = service(clock)
    raw_session = auth.login("correct horse battery staple", "https://localhost:3443")
    token = auth.issue_csrf(raw_session)
    auth.logout(raw_session, token, "https://localhost:3443")

    with pytest.raises(SessionRejected):
        auth.authenticate(raw_session)
    with pytest.raises((CommandGuardRejected, SessionRejected)):
        auth.consume_command_guard(raw_session, token, "https://localhost:3443")


def test_in_memory_invalid_or_replayed_csrf_does_not_touch_session() -> None:
    clock = Clock()
    repository = InMemorySecurityRepository()
    auth = LocalOperatorSecurity(
        make_password_verifier("correct horse battery staple"),
        "https://localhost:3443",
        repository=repository,
        clock=clock,
    )
    raw_session = auth.login("correct horse battery staple", "https://localhost:3443")
    valid_csrf = auth.issue_csrf(raw_session)
    session_digest = token_digest(raw_session)
    before_invalid = repository.get_session(session_digest)
    assert before_invalid is not None

    clock.value += timedelta(minutes=1)
    with pytest.raises(CommandGuardRejected):
        auth.consume_command_guard(
            raw_session,
            "unknown-csrf",
            "https://localhost:3443",
        )
    assert repository.get_session(session_digest) == before_invalid

    auth.consume_command_guard(raw_session, valid_csrf, "https://localhost:3443")
    after_valid = repository.get_session(session_digest)
    assert after_valid is not None and after_valid.last_seen_at == clock.value

    clock.value += timedelta(minutes=1)
    with pytest.raises(CommandGuardRejected):
        auth.consume_command_guard(raw_session, valid_csrf, "https://localhost:3443")
    assert repository.get_session(session_digest) == after_valid


def test_in_memory_command_guard_and_logout_serialize_in_both_orders() -> None:
    class PausingRepository(InMemorySecurityRepository):
        def __init__(self) -> None:
            super().__init__()
            self.pause_effect: str | None = None
            self.session_locked = Event()
            self.release_session = Event()

        def pause_next(self, effect: str) -> None:
            self.pause_effect = effect
            self.session_locked.clear()
            self.release_session.clear()

        def _after_command_session_locked(self, *, revoke_session: bool) -> None:
            effect = "logout" if revoke_session else "command"
            if self.pause_effect == effect:
                self.session_locked.set()
                if not self.release_session.wait(timeout=5):
                    raise TimeoutError("session lock was not released")
                self.pause_effect = None

        def csrf_was_consumed(self, raw_csrf: str) -> bool:
            with self._lock:
                return self._csrf[token_digest(raw_csrf)].consumed_at is not None

    clock = Clock()
    repository = PausingRepository()
    auth = LocalOperatorSecurity(
        make_password_verifier("correct horse battery staple"),
        "https://localhost:3443",
        repository=repository,
        clock=clock,
    )
    raw_session = auth.login("correct horse battery staple", "https://localhost:3443")
    command_csrf = auth.issue_csrf(raw_session)
    logout_csrf = auth.issue_csrf(raw_session)
    command_done = Event()
    logout_done = Event()
    errors: list[type[Exception]] = []

    def command() -> None:
        try:
            auth.consume_command_guard(
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
            auth.logout(raw_session, logout_csrf, "https://localhost:3443")
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
    assert repository.csrf_was_consumed(logout_csrf) is True
    assert repository.csrf_was_consumed(command_csrf) is True

    raw_session = auth.login("correct horse battery staple", "https://localhost:3443")
    command_csrf = auth.issue_csrf(raw_session)
    logout_csrf = auth.issue_csrf(raw_session)
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
    assert repository.csrf_was_consumed(logout_csrf) is True
    assert repository.csrf_was_consumed(command_csrf) is False
    with pytest.raises(SessionRejected):
        auth.authenticate(raw_session)
