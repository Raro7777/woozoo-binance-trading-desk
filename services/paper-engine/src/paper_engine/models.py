"""Immutable values for Paper orders, fills, FIFO lots and journals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import re


OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


def validate_opaque_id(value: str, *, max_length: int = 128) -> None:
    """Keep Python and PostgreSQL canonical event hashing byte-identical."""
    if not value.isascii() or len(value) > max_length or OPAQUE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("INVALID_OPAQUE_ID")


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
        validate_opaque_id(self.authorization_id, max_length=64)
        validate_opaque_id(self.authorization_nonce)


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    request_hash: str
    outcome: str
    order_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MarkFixture:
    price: str
    source: str
    as_of: datetime
    quality: str
    policy_version: str

    def validate(self) -> None:
        if not self.source or not self.policy_version or self.quality != "VALID":
            raise ValueError("INVALID_MARK_PROVENANCE")
        if self.as_of.tzinfo is None:
            raise ValueError("INVALID_MARK_PROVENANCE")


@dataclass(frozen=True, slots=True)
class UnrealizedPnl:
    asset: str
    amount: Decimal
    mark: MarkFixture
    fifo_policy_version: str = "fifo-v1"


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
    authorization_namespace: str = "test"
    order_type: str = "LIMIT"
    time_in_force: str = "GTC"


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
    fee_policy_version: str = "quote-fee-v1"
    symbol_rule_version: str = "spot-public-rules-2026-07-19"


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
        if len(self.entries) < 2:
            raise ValueError("LEDGER_JOURNAL_INCOMPLETE")
        for entry in self.entries:
            if entry.debit < 0 or entry.credit < 0:
                raise ValueError("LEDGER_NEGATIVE_ENTRY")
            if (entry.debit > 0) == (entry.credit > 0):
                raise ValueError("LEDGER_ENTRY_MUST_BE_ONE_SIDED")
        commodities = {entry.commodity for entry in self.entries}
        for commodity in commodities:
            debit = sum((e.debit for e in self.entries if e.commodity == commodity), Decimal(0))
            credit = sum((e.credit for e in self.entries if e.commodity == commodity), Decimal(0))
            if debit != credit:
                raise ValueError(f"LEDGER_IMBALANCE:{commodity}")
