"""Deterministic Testnet preview, Risk, and multi-commodity journal primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Literal, cast

from .canonical import CanonicalValue, canonical_digest, derive_client_order_id


SCALE = Decimal("0.000000000000000001")
FEE_RATE = Decimal("0.001")


def decimal_value(raw: str, *, allow_zero: bool = False) -> Decimal:
    if not isinstance(raw, str):
        raise TypeError("FINANCIAL_VALUE_MUST_BE_DECIMAL_STRING")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("FINANCIAL_VALUE_INVALID") from error
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise ValueError("FINANCIAL_VALUE_INVALID")
    return value


def decimal_text(value: Decimal) -> str:
    return format(value.quantize(SCALE), "f")


@dataclass(frozen=True, slots=True)
class TestnetRiskContext:
    __test__ = False

    proposal_id: str
    proposal_hash: str
    evidence_id: str
    evidence_digest: str
    data_state_digest: str
    account_binding_id: str
    account_generation: int
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    side: Literal["BUY", "SELL"]
    quantity: str
    limit_price: str
    available_quote: str
    available_base: str
    symbol_rules_digest: str
    ledger_snapshot_digest: str
    reconciliation_checkpoint_digest: str
    paper_kill_version: int
    testnet_barrier_version: int
    as_of: datetime
    knowledge_cutoff: datetime
    expires_at: datetime
    data_healthy: bool
    paper_kill_active: bool
    testnet_barrier_active: bool
    reconciliation_healthy: bool
    ledger_healthy: bool
    source_risk_decision_id: str = ""
    source_risk_decision_hash: str = ""


@dataclass(frozen=True, slots=True)
class TestnetRiskResult:
    verdict: Literal["ALLOWED", "DENIED", "ERROR"]
    ordered_reason_codes: tuple[str, ...]
    risk_input_digest: str
    preview: dict[str, object] | None


def testnet_risk_input(context: TestnetRiskContext) -> dict[str, object]:
    """Return the complete canonical material revalidated before command creation."""

    return {
        "schema_version": "woozoo.testnet-risk-input/v1",
        "proposal_id": context.proposal_id,
        "proposal_hash": context.proposal_hash,
        "evidence_id": context.evidence_id,
        "evidence_digest": context.evidence_digest,
        "data_state_digest": context.data_state_digest,
        "account_binding_id": context.account_binding_id,
        "account_generation": context.account_generation,
        "symbol": context.symbol,
        "side": context.side,
        "quantity": context.quantity,
        "limit_price": context.limit_price,
        "available_quote": context.available_quote,
        "available_base": context.available_base,
        "symbol_rules_digest": context.symbol_rules_digest,
        "ledger_snapshot_digest": context.ledger_snapshot_digest,
        "reconciliation_checkpoint_digest": context.reconciliation_checkpoint_digest,
        "paper_kill_version": context.paper_kill_version,
        "testnet_barrier_version": context.testnet_barrier_version,
        "as_of": context.as_of.isoformat(),
        "knowledge_cutoff": context.knowledge_cutoff.isoformat(),
        "expires_at": context.expires_at.isoformat(),
        "data_healthy": context.data_healthy,
        "paper_kill_active": context.paper_kill_active,
        "testnet_barrier_active": context.testnet_barrier_active,
        "reconciliation_healthy": context.reconciliation_healthy,
        "ledger_healthy": context.ledger_healthy,
        "source_risk_decision_id": context.source_risk_decision_id,
        "source_risk_decision_hash": context.source_risk_decision_hash,
    }


def evaluate_testnet_risk(context: TestnetRiskContext) -> TestnetRiskResult:
    input_digest = canonical_digest(cast(CanonicalValue, testnet_risk_input(context)))
    reasons: list[str] = []
    if not context.data_healthy:
        reasons.append("DATA_UNHEALTHY")
    if context.paper_kill_active:
        reasons.append("PAPER_KILL_ACTIVE")
    if context.testnet_barrier_active:
        reasons.append("TESTNET_BARRIER_ACTIVE")
    if not context.reconciliation_healthy:
        reasons.append("RECONCILIATION_UNHEALTHY")
    if not context.ledger_healthy:
        reasons.append("LEDGER_UNHEALTHY")
    if context.expires_at <= context.as_of or context.knowledge_cutoff > context.as_of:
        reasons.append("TIME_BOUND_INVALID")
    try:
        quantity = decimal_value(context.quantity)
        price = decimal_value(context.limit_price)
        available_quote = decimal_value(context.available_quote, allow_zero=True)
        available_base = decimal_value(context.available_base, allow_zero=True)
    except (TypeError, ValueError):
        return TestnetRiskResult("ERROR", ("DECIMAL_INVALID",), input_digest, None)
    notional = quantity * price
    fee = (notional * FEE_RATE).quantize(SCALE, rounding=ROUND_UP)
    if context.side == "BUY" and notional + fee > available_quote:
        reasons.append("INSUFFICIENT_QUOTE")
    if context.side == "SELL" and quantity > available_base:
        reasons.append("INSUFFICIENT_BASE")
    client_order_id = derive_client_order_id(
        context.proposal_hash, input_digest, context.account_generation
    )
    preview: dict[str, object] = {
        "schema_version": "woozoo.testnet-order-preview/v1",
        "environment": "BINANCE_SPOT_TESTNET",
        "account_binding_id": context.account_binding_id,
        "account_generation": context.account_generation,
        "proposal_id": context.proposal_id,
        "proposal_hash": context.proposal_hash,
        "evidence_id": context.evidence_id,
        "evidence_digest": context.evidence_digest,
        "data_state_digest": context.data_state_digest,
        "as_of": context.as_of.isoformat().replace("+00:00", "Z"),
        "knowledge_cutoff": context.knowledge_cutoff.isoformat().replace("+00:00", "Z"),
        "symbol": context.symbol,
        "side": context.side,
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "quantity": decimal_text(quantity),
        "limit_price": decimal_text(price),
        "worst_case_notional": decimal_text(notional),
        "fee_reserve": decimal_text(fee),
        "preview_policy_version": "woozoo.testnet-order-preview-policy/v1",
        "calculator_version": "woozoo.decimal-calculator/v1",
        "symbol_rules_digest": context.symbol_rules_digest,
        "ledger_snapshot_digest": context.ledger_snapshot_digest,
        "reconciliation_checkpoint_digest": context.reconciliation_checkpoint_digest,
        "paper_kill_version": context.paper_kill_version,
        "testnet_barrier_version": context.testnet_barrier_version,
        "client_order_id": client_order_id,
        "created_at": context.as_of.isoformat().replace("+00:00", "Z"),
        "expires_at": context.expires_at.isoformat().replace("+00:00", "Z"),
    }
    preview["testnet_order_preview_digest"] = canonical_digest(cast(CanonicalValue, preview))
    if reasons:
        return TestnetRiskResult("DENIED", tuple(reasons), input_digest, preview)
    return TestnetRiskResult("ALLOWED", ("RISK_ALLOWED",), input_digest, preview)


@dataclass(frozen=True, slots=True)
class JournalLine:
    account: str
    commodity: str
    debit: Decimal
    credit: Decimal


class MultiCommodityLedger:
    """Append-only idempotent journal that balances each commodity independently."""

    def __init__(self) -> None:
        self.transactions: dict[str, tuple[JournalLine, ...]] = {}

    def post(self, business_event_id: str, lines: tuple[JournalLine, ...]) -> bool:
        if business_event_id in self.transactions:
            if self.transactions[business_event_id] != lines:
                raise ValueError("LEDGER_EVENT_CONFLICT")
            return False
        if len(lines) < 2:
            raise ValueError("LEDGER_LINES_INCOMPLETE")
        totals: dict[str, tuple[Decimal, Decimal]] = {}
        for line in lines:
            if (
                not line.debit.is_finite()
                or not line.credit.is_finite()
                or line.debit < 0
                or line.credit < 0
                or (line.debit > 0) == (line.credit > 0)
            ):
                raise ValueError("LEDGER_LINE_INVALID")
            debit, credit = totals.get(line.commodity, (Decimal(0), Decimal(0)))
            totals[line.commodity] = (debit + line.debit, credit + line.credit)
        if any(debit != credit for debit, credit in totals.values()):
            raise ValueError("LEDGER_UNBALANCED")
        self.transactions[business_event_id] = lines
        return True
