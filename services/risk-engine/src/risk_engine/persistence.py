"""PostgreSQL persistence for immutable Phase 5 Risk decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from enum import StrEnum
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from platform_core import canonical_hash

from .engine import APPROVED_POLICY_BODY, APPROVED_POLICY_HASH, evaluate_risk
from .models import RiskDecision


class RiskPersistenceStage(StrEnum):
    DECISION = "decision"
    OUTBOX = "outbox"


@dataclass(frozen=True, slots=True)
class PersistedRiskDecision:
    created: bool
    decision_id: str
    risk_input_digest: str
    decision_hash: str
    outbox_event_id: str


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("INVALID_RISK_INPUT")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("INVALID_DECISION_CLOCK")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("INVALID_DECISION_CLOCK")
    return parsed


def _event_id(event_type: str, aggregate_id: str, version: int) -> str:
    return canonical_hash(["event", event_type, aggregate_id, str(version)])


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _as_object(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(code)
    return value


def _assemble_risk_input(context: dict[str, object]) -> dict[str, object]:
    existing = context.get("existing_risk_input")
    if isinstance(existing, dict):
        return cast(dict[str, object], existing)
    proposal = _as_object(context.get("proposal"), "RISK_PROPOSAL_CONTEXT_INVALID")
    evidence = _as_object(context.get("evidence"), "RISK_EVIDENCE_CONTEXT_INVALID")
    balances = _as_object(context.get("balances"), "RISK_BALANCE_CONTEXT_INVALID")
    books = _as_object(context.get("books"), "RISK_BOOK_CONTEXT_INVALID")
    reconciliation = _as_object(context.get("reconciliation"), "RISK_RECONCILIATION_REQUIRED")
    kill = _as_object(context.get("kill_switch"), "RISK_KILL_CONTEXT_INVALID")
    recorded_at_raw = context.get("recorded_at")
    if not isinstance(recorded_at_raw, str):
        raise ValueError("RISK_RECORDED_AT_INVALID")
    recorded_at = _timestamp(recorded_at_raw)
    symbol = cast(str, proposal.get("symbol"))
    side = cast(str, proposal.get("side"))
    if symbol not in {"BTCUSDT", "ETHUSDT"} or side not in {"BUY", "SELL"}:
        raise ValueError("RISK_PROPOSAL_NOT_ELIGIBLE")
    book = _as_object(books.get(symbol), "RISK_BOOK_NOT_FOUND")
    bid = Decimal(cast(str, book.get("best_bid")))
    ask = Decimal(cast(str, book.get("best_ask")))
    if bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError("RISK_BOOK_INVALID")
    fee_rate = Decimal("0.001")
    equity = Decimal(
        cast(str, _as_object(balances.get("USDT"), "USDT_BALANCE_MISSING")["available"])
    )
    equity += Decimal(cast(str, _as_object(balances["USDT"], "USDT_BALANCE_MISSING")["held"]))
    positions: dict[str, object] = {}
    market_books: dict[str, object] = {}
    for asset, position_symbol in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        balance = _as_object(balances.get(asset), f"{asset}_BALANCE_MISSING")
        position_book = _as_object(books.get(position_symbol), f"{asset}_BOOK_NOT_FOUND")
        midpoint = (
            Decimal(cast(str, position_book["best_bid"]))
            + Decimal(cast(str, position_book["best_ask"]))
        ) / 2
        available = Decimal(cast(str, balance["available"]))
        held = Decimal(cast(str, balance["held"]))
        equity += (available + held) * midpoint
        positions[position_symbol] = {
            "available": _decimal_text(available),
            "held": _decimal_text(held),
            "midpoint": _decimal_text(midpoint),
        }
        market_books[position_symbol] = {
            "best_bid": _decimal_text(Decimal(cast(str, position_book["best_bid"]))),
            "best_ask": _decimal_text(Decimal(cast(str, position_book["best_ask"]))),
        }
    tick = Decimal("0.01")
    price = (ask if side == "BUY" else bid).quantize(tick, rounding=ROUND_DOWN)
    step = Decimal("0.00001") if symbol == "BTCUSDT" else Decimal("0.0001")
    min_quantity = step
    available_resource = (
        Decimal(cast(str, _as_object(balances["USDT"], "USDT_BALANCE_MISSING")["available"]))
        if side == "BUY"
        else Decimal(
            cast(
                str,
                _as_object(
                    balances["BTC" if symbol == "BTCUSDT" else "ETH"], "BASE_BALANCE_MISSING"
                )["available"],
            )
        )
        * price
    )
    gross_cap = min(available_resource, equity * Decimal("0.0025"))
    quantity = (gross_cap / ((Decimal(1) + fee_rate) * price)).quantize(step, rounding=ROUND_DOWN)
    if quantity < min_quantity or quantity * price < Decimal("5"):
        raise ValueError("PAPER_PREVIEW_RESOURCES_INSUFFICIENT")
    principal = quantity * price
    fee = principal * fee_rate
    worst_notional = principal + fee
    preview_body: dict[str, object] = {
        "symbol": symbol,
        "side": side,
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "quantity": _decimal_text(quantity),
        "limit_price": _decimal_text(price),
        "worst_case_fee": _decimal_text(fee),
        "worst_case_hold": _decimal_text(worst_notional if side == "BUY" else quantity),
        "worst_case_notional": _decimal_text(worst_notional),
        "best_bid": _decimal_text(bid),
        "best_ask": _decimal_text(ask),
        "expected_slippage_inputs": {"method": "limit-vs-book-v1"},
    }
    open_orders = context.get("open_orders")
    fifo_lots = context.get("fifo_lots")
    if not isinstance(open_orders, list) or not isinstance(fifo_lots, list):
        raise ValueError("RISK_PORTFOLIO_CONTEXT_INVALID")
    portfolio_body: dict[str, object] = {
        "snapshot_id": canonical_hash(["paper-portfolio", context["recorded_at"]]),
        "available_quote": cast(
            str, _as_object(balances["USDT"], "USDT_BALANCE_MISSING")["available"]
        ),
        "held_quote": cast(str, _as_object(balances["USDT"], "USDT_BALANCE_MISSING")["held"]),
        "fee_liabilities": "0",
        "positions": positions,
        "fifo_lots": fifo_lots,
        "realized_pnl_24h": cast(str, context.get("realized_pnl_24h")),
        "high_water_equity": _decimal_text(
            max(equity, Decimal(cast(str, context["prior_high_water"])))
        ),
        "open_orders": open_orders,
    }
    exposure_body: dict[str, object] = {
        "snapshot_id": canonical_hash(["paper-exposure", context["recorded_at"]]),
        "open_order_count": len(open_orders),
    }
    evidence_as_of = _timestamp(evidence["as_of"])
    knowledge_cutoff = _timestamp(evidence["knowledge_cutoff"])
    # PostgreSQL renders UTC timestamptz JSON values with ``+00:00`` while the
    # immutable Agent payload uses canonical RFC3339 ``Z``.  Normalize the
    # authoritative snapshot at the Risk boundary so exact Evidence binding is
    # representation-independent without mutating the Proposal.
    as_of = evidence_as_of.isoformat().replace("+00:00", "Z")
    cutoff = knowledge_cutoff.isoformat().replace("+00:00", "Z")
    book_times = []
    book_healthy = True
    for position_symbol in ("BTCUSDT", "ETHUSDT"):
        position_book = _as_object(books[position_symbol], "RISK_BOOK_CONTEXT_INVALID")
        book_times.extend(
            [_timestamp(position_book["event_time"]), _timestamp(position_book["received_at"])]
        )
        book_healthy = book_healthy and position_book.get("quality_status") == "healthy"
    temporal_future = (
        evidence_as_of > knowledge_cutoff
        or evidence_as_of > recorded_at
        or knowledge_cutoff > recorded_at
        or any(value > recorded_at for value in book_times)
    )
    all_fresh = (
        not temporal_future
        and recorded_at - evidence_as_of <= timedelta(minutes=5)
        and all(recorded_at - value <= timedelta(seconds=5) for value in book_times)
    )
    freshness = "FRESH" if all_fresh else "STALE"
    quality = str(evidence["quality_status"]).upper()
    if not book_healthy and quality == "HEALTHY":
        quality = "DEGRADED"
    return {
        "risk_input_schema_version": "woozoo.risk-input/v3",
        "namespace": "paper",
        "proposal": {
            "producer_contract": "woozoo.trade-proposal/v1",
            "schema_version": "woozoo.trade-proposal/v1",
            "payload": proposal,
            "proposal_hash": context["proposal_hash"],
        },
        "portfolio": {**portfolio_body, "snapshot_hash": canonical_hash(portfolio_body)},
        "market_books": market_books,
        "data": {
            "evidence_id": evidence["evidence_id"],
            "evidence_hash": evidence["evidence_digest"],
            "as_of": as_of,
            "knowledge_cutoff": cutoff,
            "freshness": freshness,
            "quality": quality,
            "future_contamination": temporal_future,
            "watermark_complete": quality == "HEALTHY",
        },
        "preview_policy_version": "woozoo.paper-order-preview-policy/v1",
        "order_preview": {
            **preview_body,
            "paper_order_preview_hash": canonical_hash(preview_body),
        },
        "policy": {**APPROVED_POLICY_BODY, "policy_hash": APPROVED_POLICY_HASH},
        "kill_switch": kill,
        "reconciliation": reconciliation,
        "decision_clock": {
            "decision_as_of": recorded_at.isoformat(),
            "source": "postgres-transaction-clock",
            "version": "1",
        },
        "duplicate": {
            "key": f"{symbol}:{side}",
            "window_seconds": 900,
            "proposal_seen": False,
            "order_intent_seen": bool(context.get("order_intent_seen")),
        },
        "exposure_snapshot": {
            **exposure_body,
            "snapshot_hash": canonical_hash(exposure_body),
        },
    }


class PostgresRiskStore:
    """Commits decision state and its durable outbox in one local transaction."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _fail(stage: RiskPersistenceStage, requested: RiskPersistenceStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_RISK_FAILURE:{stage.value}")

    def persist_decision(
        self,
        risk_input: dict[str, object],
        decision: RiskDecision,
        *,
        recorded_at: datetime,
        _fail_after: RiskPersistenceStage | None = None,
        _connection: psycopg.Connection[object] | None = None,
    ) -> PersistedRiskDecision:
        if recorded_at.tzinfo is None:
            raise ValueError("RECORDED_AT_MUST_BE_AWARE")
        authoritative_decision = evaluate_risk(risk_input)
        if authoritative_decision != decision:
            raise ValueError("FORGED_RISK_DECISION")
        if canonical_hash(risk_input) != decision.risk_input_digest:
            raise ValueError("RISK_INPUT_DIGEST_MISMATCH")
        expected_decision_hash = canonical_hash(
            {
                "decision_schema_version": decision.decision_schema_version,
                "risk_input_digest": decision.risk_input_digest,
                "verdict": decision.verdict,
                "ordered_reason_codes": list(decision.ordered_reason_codes),
            }
        )
        if expected_decision_hash != decision.decision_hash:
            raise ValueError("DECISION_HASH_MISMATCH")

        proposal = _object(risk_input["proposal"])
        proposal_payload = _object(proposal["payload"])
        proposal_id = (
            cast(str, proposal_payload["proposal_id"])
            if risk_input.get("risk_input_schema_version")
            in {"woozoo.risk-input/v2", "woozoo.risk-input/v3"}
            else None
        )
        portfolio = _object(risk_input["portfolio"])
        data_state = _object(risk_input["data"])
        preview = _object(risk_input["order_preview"])
        policy = _object(risk_input["policy"])
        kill = _object(risk_input["kill_switch"])
        reconciliation = _object(risk_input["reconciliation"])
        clock = _object(risk_input["decision_clock"])
        decision_id = canonical_hash(["risk-decision", decision.decision_hash])
        production = risk_input.get("risk_input_schema_version") == "woozoo.risk-input/v3"
        event_type = "risk.decision.recorded.v2" if production else "risk.decision.recorded.v1"
        event_version = 2 if production else 1
        event_id = _event_id(event_type, decision_id, 1)
        event_data: dict[str, object] = {
            "decision_schema_version": decision.decision_schema_version,
            "decision_id": decision_id,
            "risk_input_digest": decision.risk_input_digest,
            "decision_hash": decision.decision_hash,
            "verdict": decision.verdict,
            "primary_reason": decision.primary_reason_code,
            "ordered_reason_codes": list(decision.ordered_reason_codes),
            "policy_version": policy["version"],
            "proposal_hash": proposal["proposal_hash"],
            "portfolio_snapshot_hash": portfolio["snapshot_hash"],
            "data_state_hash": canonical_hash(data_state),
            "paper_order_preview_hash": preview["paper_order_preview_hash"],
            "reconciliation_checkpoint_hash": reconciliation["checkpoint_hash"],
            "kill_switch_version": kill["version"],
            "decision_as_of": clock["decision_as_of"],
        }
        payload_hash = canonical_hash(event_data)
        envelope: dict[str, object] = {
            "spec_version": "woozoo.event/v1",
            "event_id": event_id,
            "event_type": event_type,
            "event_version": event_version,
            "occurred_at": recorded_at.isoformat(),
            "producer": "risk-engine",
            "activation_phase": 7,
            "aggregate_id": decision_id,
            "aggregate_version": 1,
            "payload_hash": payload_hash,
            "data": event_data,
        }

        decision_record: dict[str, object] = {
            "decision_id": decision_id,
            "risk_input_digest": decision.risk_input_digest,
            "risk_input": risk_input,
            "decision_hash": decision.decision_hash,
            "verdict": decision.verdict,
            "primary_reason": decision.primary_reason_code,
            "ordered_reason_codes": list(decision.ordered_reason_codes),
            "policy_version": policy["version"],
            "proposal_hash": proposal["proposal_hash"],
            "portfolio_snapshot_hash": portfolio["snapshot_hash"],
            "data_state_hash": canonical_hash(data_state),
            "paper_order_preview_hash": preview["paper_order_preview_hash"],
            "reconciliation_checkpoint_hash": reconciliation["checkpoint_hash"],
            "kill_switch_version": cast(int, kill["version"]),
            "decision_as_of": _timestamp(clock["decision_as_of"]).isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "proposal_id": proposal_id,
        }

        def persist(connection: psycopg.Connection[object]) -> tuple[object, ...]:
            row = connection.execute(
                "SELECT created,outbox_event_id FROM persist_risk_decision_v1(%s::jsonb,%s::jsonb)",
                (Jsonb(decision_record), Jsonb(envelope)),
            ).fetchone()
            if row is None:
                raise RuntimeError("RISK_DECISION_COMMAND_NO_RESULT")
            self._fail(RiskPersistenceStage.DECISION, _fail_after)
            self._fail(RiskPersistenceStage.OUTBOX, _fail_after)
            return cast(tuple[object, ...], row)

        if _connection is None:
            with psycopg.connect(self.database_url) as connection:
                persisted = persist(connection)
        else:
            persisted = persist(_connection)
        return PersistedRiskDecision(
            bool(persisted[0]),
            decision_id,
            decision.risk_input_digest,
            decision.decision_hash,
            cast(str, persisted[1]),
        )

    def evaluate_proposal(
        self,
        proposal_id: str,
        paper_account_id: str,
        recorded_at: datetime,
    ) -> tuple[dict[str, object], RiskDecision, PersistedRiskDecision]:
        """Assemble, evaluate, and persist one Risk v3 decision under one DB transaction."""

        if recorded_at.tzinfo is None:
            raise ValueError("RECORDED_AT_MUST_BE_AWARE")
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT load_authoritative_risk_context_v1(%s,%s,%s)",
                (proposal_id, paper_account_id, recorded_at),
            ).fetchone()
            if row is None:
                raise RuntimeError("RISK_CONTEXT_COMMAND_NO_RESULT")
            context = _as_object(row[0], "RISK_CONTEXT_INVALID")
            risk_input = _assemble_risk_input(context)
            decision = evaluate_risk(risk_input)
            persisted = self.persist_decision(
                risk_input,
                decision,
                recorded_at=recorded_at,
                _connection=connection,
            )
        return risk_input, decision, persisted
