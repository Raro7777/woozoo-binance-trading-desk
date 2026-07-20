"""Fail-closed settings: Phase 6 supports paper + mock only."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class AgentSettings:
    trading_mode: str
    provider: str

    @classmethod
    def from_env(cls) -> "AgentSettings":
        trading_mode = os.getenv("TRADING_MODE")
        provider = os.getenv("LLM_PROVIDER")
        if trading_mode != "paper":
            raise RuntimeError("TRADING_MODE must be explicitly paper")
        if provider != "mock":
            raise RuntimeError("LLM_PROVIDER must be explicitly mock")
        return cls(trading_mode=trading_mode, provider=provider)
