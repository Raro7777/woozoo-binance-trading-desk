"""Fail-closed source selection for recorded and fixed public collection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class MarketDataSource(str, Enum):
    RECORDED = "recorded"
    SPOT_PUBLIC = "spot_public"


@dataclass(frozen=True, slots=True)
class MarketDataSettings:
    source: MarketDataSource

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "MarketDataSettings":
        raw = values.get("MARKET_DATA_SOURCE")
        try:
            source = MarketDataSource(raw) if raw is not None else None
        except ValueError as error:
            raise ValueError("MARKET_DATA_SOURCE must be recorded or spot_public") from error
        if source is None:
            raise ValueError("MARKET_DATA_SOURCE is required")
        return cls(source=source)
