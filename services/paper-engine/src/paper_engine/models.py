"""Immutable values for Paper orders, fills, FIFO lots and journals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ExecutionFixture:
    """P5 consumer contract fixture; never a production authorization."""

    authorization_id: str
    authorization_nonce: str
    preview_hash: str
    status: str = "AUTHORIZED_FIXTURE"
    namespace: str = "test"

    def validate(self) -> None:
        if self.namespace != "test" or self.status != "AUTHORIZED_FIXTURE":
            raise ValueError("PHASE_4_PRODUCTION_AUTHORIZATION_FORBIDDEN")
        if len(self.preview_hash) != 64:
            raise ValueError("INVALID_FIXTURE_PREVIEW_HASH")


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    client_order_id: str
    authorization_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    accepted_broker_seq: int
    status: OrderStatus
    filled_quantity: Decimal
    held_asset: str
    held_amount: Decimal
    version: int


@dataclass(frozen=True, slots=True)
class PaperFill:
    fill_id: str
    order_id: str
    observation_id: str
    quantity: Decimal
    price: Decimal
    fee_asset: str
    fee_rate: Decimal
    fee_amount: Decimal
    broker_seq: int


@dataclass(frozen=True, slots=True)
class FifoLot:
    lot_id: str
    asset: str
    acquired_quantity: Decimal
    quote_cost: Decimal
    source_fill_id: str
    acquired_at: datetime


@dataclass(frozen=True, slots=True)
class LotConsumption:
    consumption_id: str
    lot_id: str
    source_fill_id: str
    quantity: Decimal
    quote_basis: Decimal


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    account: str
    commodity: str
    debit: Decimal
    credit: Decimal


@dataclass(frozen=True, slots=True)
class Journal:
    journal_id: str
    business_event_type: str
    business_event_id: str
    journal_kind: str
    entries: tuple[LedgerEntry, ...]
    reversal_of: str | None = None
    replacement_for: str | None = None

    def assert_balanced(self) -> None:
        commodities = {entry.commodity for entry in self.entries}
        for commodity in commodities:
            debit = sum((e.debit for e in self.entries if e.commodity == commodity), Decimal(0))
            credit = sum((e.credit for e in self.entries if e.commodity == commodity), Decimal(0))
            if debit != credit:
                raise ValueError(f"LEDGER_IMBALANCE:{commodity}")
