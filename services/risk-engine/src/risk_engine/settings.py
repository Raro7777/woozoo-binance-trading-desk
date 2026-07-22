"""Fail-closed production settings for the deterministic Risk role."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class RiskSettings:
    trading_mode: str
    database_url: str

    @classmethod
    def from_env(cls) -> "RiskSettings":
        trading_mode = os.getenv("TRADING_MODE")
        database_url = os.getenv("RISK_DATABASE_URL")
        if trading_mode != "paper":
            raise RuntimeError("TRADING_MODE must be explicitly paper")
        if not database_url:
            raise RuntimeError("RISK_DATABASE_URL is required")
        return cls(trading_mode=trading_mode, database_url=database_url)
