"""Immutable Testnet execution state values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class CommandOutcome(StrEnum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    FOUND = "FOUND"
    REJECTED = "REJECTED"
    NOT_FOUND_CONFIRMED = "NOT_FOUND_CONFIRMED"
    BLOCKED = "BLOCKED"


class OrderState(StrEnum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class AccountGeneration:
    number: int
    status: str
    checkpoint_digest: str


@dataclass(frozen=True, slots=True)
class TestnetAuthorization:
    __test__ = False

    authorization_id: str
    approval_id: str
    proposal_hash: str
    risk_decision_hash: str
    preview_digest: str
    account_binding_id: str
    account_generation: int
    client_order_id: str
    issued_at: datetime
    expires_at: datetime
    revoked: bool


@dataclass(frozen=True, slots=True)
class SubmitReceipt:
    command_id: str
    authorization_id: str
    approval_id: str
    client_order_id: str
    account_generation: int
    outcome: CommandOutcome


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    request_id: str
    command_type: str
    client_order_id: str
    account_generation: int
    reason: str


@dataclass(frozen=True, slots=True)
class OrderObservation:
    observation_id: str
    client_order_id: str
    account_generation: int
    status: str
    cumulative_filled_quantity: str
    source_sequence: int


@dataclass(frozen=True, slots=True)
class TestnetOrder:
    client_order_id: str
    account_generation: int
    status: OrderState
    filled_quantity: Decimal
    last_source_sequence: int
