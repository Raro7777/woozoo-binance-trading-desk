"""Local-only operator authentication and one-time browser command guards.

Raw session and CSRF values exist only at the browser boundary.  Durable
repositories receive SHA-256 digests, never bearer material.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import secrets
from threading import RLock
from typing import Callable, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
import psycopg


ACTOR_ID = "operator-local-1"
COOKIE_NAME = "__Host-woozoo_session"
SESSION_IDLE_TTL = timedelta(minutes=30)
SESSION_ABSOLUTE_TTL = timedelta(hours=8)
CSRF_TTL = timedelta(minutes=10)


class AuthenticationFailed(ValueError):
    """Credentials are invalid without disclosing which field failed."""


class SessionRejected(ValueError):
    """The supplied session cannot authorize a request."""


class CommandGuardRejected(ValueError):
    """Origin or one-time CSRF validation failed with effect zero."""


def token_digest(raw: str) -> str:
    return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionRecord:
    digest: str
    actor_id: str
    issued_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CsrfRecord:
    digest: str
    session_digest: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


class SecurityRepository(Protocol):
    def rotate_session(
        self, record: SessionRecord, prior_digest: str | None, now: datetime
    ) -> None: ...

    def get_session(self, digest: str) -> SessionRecord | None: ...

    def save_session(self, record: SessionRecord) -> None: ...

    def save_csrf(self, record: CsrfRecord) -> None: ...

    def consume_command_guard(
        self,
        session_digest: str,
        csrf_digest: str,
        now: datetime,
        *,
        revoke_session: bool,
    ) -> SessionRecord: ...


class InMemorySecurityRepository:
    """Thread-safe reference repository used by tests and local demo mode."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._csrf: dict[str, CsrfRecord] = {}
        self._lock = RLock()

    def rotate_session(
        self, record: SessionRecord, prior_digest: str | None, now: datetime
    ) -> None:
        with self._lock:
            if prior_digest is not None and prior_digest in self._sessions:
                prior = self._sessions[prior_digest]
                self._sessions[prior_digest] = replace(prior, revoked_at=now)
            self._sessions[record.digest] = record

    def get_session(self, digest: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.get(digest)

    def save_session(self, record: SessionRecord) -> None:
        with self._lock:
            self._sessions[record.digest] = record

    def save_csrf(self, record: CsrfRecord) -> None:
        with self._lock:
            self._csrf[record.digest] = record

    def consume_command_guard(
        self,
        session_digest: str,
        csrf_digest: str,
        now: datetime,
        *,
        revoke_session: bool,
    ) -> SessionRecord:
        with self._lock:
            session = self._sessions.get(session_digest)
            if (
                session is None
                or session.revoked_at is not None
                or now >= session.idle_expires_at
                or now >= session.absolute_expires_at
                or session.actor_id != ACTOR_ID
            ):
                raise SessionRejected("session is unavailable")
            csrf = self._csrf.get(csrf_digest)
            if (
                csrf is None
                or csrf.session_digest != session_digest
                or csrf.consumed_at is not None
                or now >= csrf.expires_at
            ):
                raise CommandGuardRejected("CSRF token unavailable")
            next_session = replace(
                session,
                last_seen_at=now,
                idle_expires_at=min(now + SESSION_IDLE_TTL, session.absolute_expires_at),
                revoked_at=now if revoke_session else None,
            )
            self._csrf[csrf_digest] = replace(csrf, consumed_at=now)
            self._sessions[session_digest] = next_session
            return next_session


class PostgresSecurityRepository:
    """Digest-only durable repository owned by the Phase 7 control-api role."""

    def __init__(self, database_url: str, password_verifier: str) -> None:
        self._database_url = database_url
        now = datetime.now(UTC)
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "INSERT INTO local_operators(actor_id,argon2id_phc,created_at,disabled_at) "
                "VALUES (%s,%s,%s,NULL) ON CONFLICT (actor_id) DO NOTHING",
                (ACTOR_ID, password_verifier, now),
            )
            stored = connection.execute(
                "SELECT argon2id_phc,disabled_at FROM local_operators WHERE actor_id=%s",
                (ACTOR_ID,),
            ).fetchone()
            if stored is None or stored[0] != password_verifier or stored[1] is not None:
                raise RuntimeError("local operator verifier state does not match bootstrap")

    def rotate_session(
        self, record: SessionRecord, prior_digest: str | None, now: datetime
    ) -> None:
        with psycopg.connect(self._database_url) as connection:
            if prior_digest is not None:
                connection.execute(
                    "UPDATE operator_sessions SET revoked_at=%s "
                    "WHERE session_digest=%s AND revoked_at IS NULL",
                    (now, prior_digest),
                )
            connection.execute(
                "INSERT INTO operator_sessions(session_digest,actor_id,issued_at,last_seen_at,"
                "idle_expires_at,absolute_expires_at,revoked_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    record.digest,
                    record.actor_id,
                    record.issued_at,
                    record.last_seen_at,
                    record.idle_expires_at,
                    record.absolute_expires_at,
                    record.revoked_at,
                ),
            )

    def get_session(self, digest: str) -> SessionRecord | None:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT session_digest,actor_id,issued_at,last_seen_at,idle_expires_at,"
                "absolute_expires_at,revoked_at FROM operator_sessions WHERE session_digest=%s",
                (digest,),
            ).fetchone()
        return SessionRecord(*row) if row is not None else None

    def save_session(self, record: SessionRecord) -> None:
        with psycopg.connect(self._database_url) as connection:
            if record.revoked_at is None:
                changed = connection.execute(
                    "UPDATE operator_sessions SET last_seen_at=%s,idle_expires_at=%s "
                    "WHERE session_digest=%s AND revoked_at IS NULL",
                    (record.last_seen_at, record.idle_expires_at, record.digest),
                ).rowcount
            else:
                changed = connection.execute(
                    "UPDATE operator_sessions SET revoked_at=%s "
                    "WHERE session_digest=%s AND revoked_at IS NULL",
                    (record.revoked_at, record.digest),
                ).rowcount
            if changed != 1:
                raise SessionRejected("session is unavailable")

    def save_csrf(self, record: CsrfRecord) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                "INSERT INTO session_csrf_tokens(csrf_token_digest,session_digest,issued_at,"
                "expires_at,consumed_at) VALUES (%s,%s,%s,%s,NULL)",
                (record.digest, record.session_digest, record.issued_at, record.expires_at),
            )

    def consume_command_guard(
        self,
        session_digest: str,
        csrf_digest: str,
        now: datetime,
        *,
        revoke_session: bool,
    ) -> SessionRecord:
        with psycopg.connect(self._database_url) as connection:
            session_row = connection.execute(
                "SELECT session_digest,actor_id,issued_at,last_seen_at,idle_expires_at,"
                "absolute_expires_at,revoked_at FROM operator_sessions "
                "WHERE session_digest=%s FOR UPDATE",
                (session_digest,),
            ).fetchone()
            session = SessionRecord(*session_row) if session_row is not None else None
            if (
                session is None
                or session.revoked_at is not None
                or now >= session.idle_expires_at
                or now >= session.absolute_expires_at
                or session.actor_id != ACTOR_ID
            ):
                raise SessionRejected("session is unavailable")
            consumed = connection.execute(
                "UPDATE session_csrf_tokens SET consumed_at=%s WHERE csrf_token_digest=%s "
                "AND session_digest=%s AND consumed_at IS NULL AND expires_at>%s "
                "RETURNING csrf_token_digest",
                (now, csrf_digest, session_digest, now),
            ).fetchone()
            if consumed is None:
                raise CommandGuardRejected("CSRF token unavailable")
            next_idle = min(now + SESSION_IDLE_TTL, session.absolute_expires_at)
            if revoke_session:
                updated = connection.execute(
                    "UPDATE operator_sessions SET last_seen_at=%s,idle_expires_at=%s,"
                    "revoked_at=%s WHERE session_digest=%s AND revoked_at IS NULL "
                    "RETURNING session_digest,actor_id,issued_at,last_seen_at,idle_expires_at,"
                    "absolute_expires_at,revoked_at",
                    (now, next_idle, now, session_digest),
                ).fetchone()
            else:
                updated = connection.execute(
                    "UPDATE operator_sessions SET last_seen_at=%s,idle_expires_at=%s "
                    "WHERE session_digest=%s AND revoked_at IS NULL "
                    "RETURNING session_digest,actor_id,issued_at,last_seen_at,idle_expires_at,"
                    "absolute_expires_at,revoked_at",
                    (now, next_idle, session_digest),
                ).fetchone()
            if updated is None:
                raise SessionRejected("session is unavailable")
        return SessionRecord(*updated)


class LocalOperatorSecurity:
    def __init__(
        self,
        password_verifier: str,
        origin: str,
        repository: SecurityRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not origin.startswith("https://"):
            raise ValueError("operator origin must use HTTPS")
        if not password_verifier.startswith("$argon2id$"):
            raise ValueError("operator verifier must be an Argon2id PHC string")
        self._password_verifier = password_verifier
        self._origin = origin.rstrip("/")
        self._repository = repository or InMemorySecurityRepository()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._hasher = PasswordHasher()

    @property
    def origin(self) -> str:
        return self._origin

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("security clock must be timezone-aware")
        return value.astimezone(UTC)

    def login(
        self, password: str, request_origin: str | None, prior_raw_session: str | None = None
    ) -> str:
        if request_origin != self._origin:
            raise CommandGuardRejected("exact HTTPS origin required")
        try:
            verified: bool = self._hasher.verify(self._password_verifier, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            verified = False
        if not verified:
            raise AuthenticationFailed("invalid credentials")

        now = self._now()
        raw = secrets.token_urlsafe(48)
        digest = token_digest(raw)
        record = SessionRecord(
            digest=digest,
            actor_id=ACTOR_ID,
            issued_at=now,
            last_seen_at=now,
            idle_expires_at=now + SESSION_IDLE_TTL,
            absolute_expires_at=now + SESSION_ABSOLUTE_TTL,
        )
        prior_digest = token_digest(prior_raw_session) if prior_raw_session else None
        self._repository.rotate_session(record, prior_digest, now)
        return raw

    def authenticate(self, raw_session: str | None, *, touch: bool = True) -> SessionRecord:
        if not raw_session:
            raise SessionRejected("authentication required")
        digest = token_digest(raw_session)
        record = self._repository.get_session(digest)
        now = self._now()
        if (
            record is None
            or record.revoked_at is not None
            or now >= record.idle_expires_at
            or now >= record.absolute_expires_at
            or record.actor_id != ACTOR_ID
        ):
            raise SessionRejected("session is unavailable")
        if touch:
            next_idle = min(now + SESSION_IDLE_TTL, record.absolute_expires_at)
            record = replace(record, last_seen_at=now, idle_expires_at=next_idle)
            self._repository.save_session(record)
        return record

    def issue_csrf(self, raw_session: str | None) -> str:
        raw, _session, _record = self.issue_csrf_bundle(raw_session)
        return raw

    def issue_csrf_bundle(self, raw_session: str | None) -> tuple[str, SessionRecord, CsrfRecord]:
        session = self.authenticate(raw_session)
        now = self._now()
        raw = secrets.token_urlsafe(32)
        record = CsrfRecord(
            digest=token_digest(raw),
            session_digest=session.digest,
            issued_at=now,
            expires_at=now + CSRF_TTL,
        )
        self._repository.save_csrf(record)
        return raw, session, record

    def consume_command_guard(
        self, raw_session: str | None, raw_csrf: str | None, request_origin: str | None
    ) -> SessionRecord:
        return self._consume_command_guard(
            raw_session,
            raw_csrf,
            request_origin,
            revoke_session=False,
        )

    def _consume_command_guard(
        self,
        raw_session: str | None,
        raw_csrf: str | None,
        request_origin: str | None,
        *,
        revoke_session: bool,
    ) -> SessionRecord:
        if request_origin != self._origin or not raw_csrf:
            raise CommandGuardRejected("origin and CSRF token required")
        if not raw_session:
            raise SessionRejected("authentication required")
        return self._repository.consume_command_guard(
            token_digest(raw_session),
            token_digest(raw_csrf),
            self._now(),
            revoke_session=revoke_session,
        )

    def logout(
        self, raw_session: str | None, raw_csrf: str | None, request_origin: str | None
    ) -> None:
        self._consume_command_guard(
            raw_session,
            raw_csrf,
            request_origin,
            revoke_session=True,
        )


def make_password_verifier(password: str) -> str:
    """Bootstrap helper; callers must source the password from a local secret file."""

    return PasswordHasher().hash(password)
