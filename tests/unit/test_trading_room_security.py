from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest

from control_api.security import (
    ACTOR_ID,
    CommandGuardRejected,
    InMemorySecurityRepository,
    LocalOperatorSecurity,
    SessionRecord,
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


def test_in_memory_logout_wins_before_a_waiting_command_guard_atomically() -> None:
    class PausingRepository(InMemorySecurityRepository):
        def __init__(self) -> None:
            super().__init__()
            self.command_waiting = Event()
            self.release_command = Event()

        def consume_command_guard(
            self,
            session_digest: str,
            csrf_digest: str,
            now: datetime,
            *,
            revoke_session: bool,
        ) -> SessionRecord:
            if not revoke_session:
                self.command_waiting.set()
                if not self.release_command.wait(timeout=5):
                    raise TimeoutError("command guard was not released")
            return super().consume_command_guard(
                session_digest,
                csrf_digest,
                now,
                revoke_session=revoke_session,
            )

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
    rejected: list[type[Exception]] = []

    def command() -> None:
        try:
            auth.consume_command_guard(
                raw_session,
                command_csrf,
                "https://localhost:3443",
            )
        except Exception as error:  # noqa: BLE001 - thread result is asserted below
            rejected.append(type(error))

    command_thread = Thread(target=command)
    command_thread.start()
    assert repository.command_waiting.wait(timeout=5)
    try:
        auth.logout(raw_session, logout_csrf, "https://localhost:3443")
    finally:
        repository.release_command.set()
        command_thread.join(timeout=5)

    assert not command_thread.is_alive()
    assert rejected == [SessionRejected]
    assert repository.csrf_was_consumed(logout_csrf) is True
    assert repository.csrf_was_consumed(command_csrf) is False
    with pytest.raises(SessionRejected):
        auth.authenticate(raw_session)
