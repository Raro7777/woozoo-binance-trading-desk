"""Bounded reconnect and rate-limit state without wall-clock sleeps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    initial_seconds: float = 1.0
    maximum_seconds: float = 30.0
    maximum_attempts: int = 10
    cooldown: timedelta = timedelta(minutes=5)

    def delay_seconds(self, attempt: int, *, immediate: bool = False, jitter: float = 0.0) -> float:
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        if not -0.25 <= jitter <= 0.25:
            raise ValueError("jitter must be between -0.25 and 0.25")
        if immediate and attempt == 0:
            return 0.0
        exponent = max(0, attempt - 1)
        base = min(self.maximum_seconds, self.initial_seconds * (2.0**exponent))
        return float(min(self.maximum_seconds, max(0.0, base * (1 + jitter))))

    def cooldown_after_attempt(self, attempt: int) -> timedelta | None:
        return self.cooldown if attempt >= self.maximum_attempts else None


class RateLimitGuard:
    def __init__(self) -> None:
        self.blocked_until: datetime | None = None
        self.observed_418 = False

    def observe(self, status: int, headers: Mapping[str, str], now: datetime) -> None:
        if status not in {418, 429}:
            return
        header_value = next(
            (value for name, value in headers.items() if name.lower() == "retry-after"),
            None,
        )
        if header_value is None:
            seconds = 30
        else:
            try:
                seconds = int(header_value)
            except ValueError as error:
                raise ValueError("Retry-After must be an integer number of seconds") from error
            if seconds < 0:
                raise ValueError("Retry-After cannot be negative")
        self.blocked_until = now + timedelta(seconds=seconds)
        self.observed_418 = self.observed_418 or status == 418

    def may_call(self, now: datetime) -> bool:
        return self.blocked_until is None or now >= self.blocked_until
