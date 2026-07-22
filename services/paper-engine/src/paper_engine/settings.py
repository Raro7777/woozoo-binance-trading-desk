"""Fail-closed Phase 4 settings with no exchange or network capability."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PaperSettings:
    trading_mode: str
    database_url: str

    @classmethod
    def load(cls, environment: Mapping[str, str] | None = None) -> PaperSettings:
        values = environ if environment is None else environment
        if values.get("TRADING_MODE") != "paper":
            raise ValueError("TRADING_MODE must be explicitly paper")
        database_url = values.get("PAPER_DATABASE_URL", "")
        if not database_url.startswith("postgresql"):
            raise ValueError("PAPER_DATABASE_URL must be a dedicated Postgres URL")
        forbidden = {
            key
            for key in values
            if any(word in key.lower() for word in ("api_key", "secret", "testnet", "private_key"))
        }
        if forbidden:
            raise ValueError("exchange or credential configuration is forbidden")
        return cls("paper", database_url)
