from datetime import UTC, datetime, timedelta

import pytest

from control_api.security import (
    ACTOR_ID,
    CommandGuardRejected,
    LocalOperatorSecurity,
    SessionRejected,
    make_password_verifier,
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
