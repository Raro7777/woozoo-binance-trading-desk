"""Strict, server-side configuration for the Phase 1 platform shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PlatformSettings:
    trading_mode: str
    database_url: str | None
    redis_url: str | None

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "PlatformSettings":
        mode = values.get("TRADING_MODE")
        if mode != "paper":
            raise ValueError("TRADING_MODE must be explicitly set to paper")

        return cls(
            trading_mode=mode,
            database_url=values.get("DATABASE_URL") or None,
            redis_url=values.get("REDIS_URL") or None,
        )

    def require_service_dependencies(self) -> "PlatformSettings":
        if self.database_url is None or self.redis_url is None:
            raise ValueError("DATABASE_URL and REDIS_URL are required before serving traffic")
        return self
