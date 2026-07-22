"""Fail-closed settings: Phase 6 supports paper + mock only."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class AgentSettings:
    trading_mode: str
    provider: str
    database_url: str

    @classmethod
    def from_env(cls) -> "AgentSettings":
        trading_mode = os.getenv("TRADING_MODE")
        provider = os.getenv("LLM_PROVIDER")
        database_url = os.getenv("AGENT_DATABASE_URL")
        if trading_mode != "paper":
            raise RuntimeError("TRADING_MODE must be explicitly paper")
        if provider != "mock":
            raise RuntimeError("LLM_PROVIDER must be explicitly mock")
        if not database_url:
            raise RuntimeError("AGENT_DATABASE_URL is required")
        return cls(trading_mode=trading_mode, provider=provider, database_url=database_url)
