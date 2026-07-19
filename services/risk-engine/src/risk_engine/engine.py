"""Pure deterministic Phase 5 risk evaluation.

This module owns no clock, database, network, approval, authorization, or order
capability. Callers must supply the complete schema-versioned point-in-time input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from hashlib import sha256
import json
import re

from platform_core import canonical_hash

from .models import RiskDecision


INPUT_VERSION = "woozoo.risk-input/v1"
DECISION_VERSION = "woozoo.risk-decision/v1"
PROPOSAL_FIXTURE_CONTRACT = "p6-trade-proposal-consumer/v1"
POLICY_VERSION = "woozoo.risk-policy/v1"
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_SIGNED_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NUMERIC_SCALE = Decimal("0.000000000000000001")

REASON_PRIORITY: dict[str, int] = {
    "RISK_POLICY_MISSING": 10,
    "RISK_POLICY_HASH_MISMATCH": 10,
    "DECIMAL_POLICY_INVALID": 10,
    "INPUT_SCHEMA_INVALID": 10,
    "EQUITY_INVALID": 10,
    "KILL_SWITCH_ACTIVE": 20,
    "RECONCILIATION_UNHEALTHY": 30,
    "LEDGER_IMBALANCE": 30,
    "PORTFOLIO_SNAPSHOT_MISMATCH": 30,
    "EVIDENCE_MISSING": 40,
    "DATA_INVALID": 40,
    "DATA_STALE": 40,
    "FUTURE_CONTAMINATION": 40,
    "WATERMARK_INCOMPLETE": 40,
    "PROPOSAL_HASH_MISMATCH": 50,
    "DUPLICATE_PROPOSAL": 50,
    "DUPLICATE_ORDER_INTENT": 50,
    "SYMBOL_NOT_ALLOWED": 60,
    "ORDER_TYPE_NOT_LIMIT": 60,
    "SIDE_NOT_LONG_CASH": 60,
    "TIF_NOT_ALLOWED": 60,
    "INVALID_PRICE_OR_QTY": 70,
    "INSUFFICIENT_AVAILABLE_BALANCE": 70,
    "FEE_RESERVE_INSUFFICIENT": 70,
    "SELL_EXCEEDS_POSITION": 70,
    "EXECUTION_QUALITY_UNKNOWN": 75,
    "SPREAD_LIMIT_EXCEEDED": 75,
    "EXPECTED_SLIPPAGE_LIMIT_EXCEEDED": 75,
    "ORDER_NOTIONAL_LIMIT_EXCEEDED": 80,
    "SYMBOL_EXPOSURE_LIMIT_EXCEEDED": 80,
    "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED": 80,
    "REALIZED_LOSS_LIMIT_EXCEEDED": 90,
    "DRAWDOWN_LIMIT_EXCEEDED": 90,
    "RISK_ALLOWED": 100,
}

_ROOT_FIELDS = {
    "risk_input_schema_version",
    "namespace",
    "proposal",
    "portfolio",
    "data",
    "order_preview",
    "policy",
    "kill_switch",
    "reconciliation",
    "decision_clock",
    "duplicate",
    "exposure_snapshot",
}

APPROVED_CALCULATORS: dict[str, object] = {
    "decimal": {"name": "numeric-38-18", "version": "1", "hash": "1" * 64},
    "fee": {"name": "quote-fee", "version": "1", "hash": "2" * 64},
    "valuation": {"name": "midpoint", "version": "1", "hash": "3" * 64},
    "spread": {"name": "midpoint-spread-bps", "version": "1", "hash": "4" * 64},
    "slippage": {"name": "limit-vs-book-bps", "version": "1", "hash": "5" * 64},
}
APPROVED_LIMITS: dict[str, object] = {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "order_types": ["LIMIT"],
    "sides": ["BUY", "SELL"],
    "time_in_force": ["GTC"],
    "max_order_notional_ratio": "0.0025",
    "symbol_exposure_ratios": {"BTCUSDT": "0.15", "ETHUSDT": "0.10"},
    "max_portfolio_exposure_ratio": "0.25",
    "realized_loss_ratio": "0.01",
    "drawdown_ratio": "0.05",
    "max_spread_bps": "25",
    "max_expected_slippage_bps": "25",
    "duplicate_window_seconds": 900,
}
APPROVED_POLICY_BODY: dict[str, object] = {
    "version": POLICY_VERSION,
    "calculators": APPROVED_CALCULATORS,
    "limits": APPROVED_LIMITS,
}
APPROVED_POLICY_HASH = canonical_hash(APPROVED_POLICY_BODY)


def _invalid_normalize(value: object) -> object:
    """Produce a deterministic diagnostic identity for non-canonical input."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return {"$invalid_float": value.hex()}
    if isinstance(value, Decimal):
        return {"$invalid_decimal": str(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _invalid_normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_invalid_normalize(item) for item in value]
    return {"$invalid_type": type(value).__qualname__}


def _input_digest(value: object) -> tuple[str, bool]:
    try:
        return canonical_hash(value), True
    except (TypeError, ValueError):
        encoded = json.dumps(
            {"schema_version": INPUT_VERSION, "invalid_input": _invalid_normalize(value)},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(encoded.encode("utf-8")).hexdigest(), False


def _object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return value


def _exact_fields(value: dict[str, object], expected: set[str]) -> bool:
    return set(value) == expected


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _decimal(value: object, *, signed: bool = False) -> Decimal:
    pattern = _SIGNED_DECIMAL if signed else _DECIMAL
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError("financial input must be a non-negative plain decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid decimal") from error
    exponent = result.as_tuple().exponent
    if not result.is_finite() or not isinstance(exponent, int) or exponent < -18:
        raise ValueError("financial input exceeds NUMERIC(38,18)")
    if max(len(result.as_tuple().digits) + exponent, 0) > 20:
        raise ValueError("financial input exceeds NUMERIC(38,18)")
    return result


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        raise ValueError("ratio denominator must be positive")
    with localcontext() as context:
        context.prec = 80
        return numerator / denominator


def _product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return left * right


def _sum(values: Sequence[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return sum(values, Decimal(0))


def _at_or_above_ratio(
    amount: Decimal, denominator: Decimal, threshold: Decimal
) -> bool:
    """Conservatively compare a NUMERIC(38,18) amount to an inclusive ratio limit.

    The threshold amount can contain more than 18 fractional digits even though
    every authoritative stored amount is scale-18. Explicit ROUND_DOWN makes the
    boundary deterministic and independent of the ambient Decimal context.
    """
    if denominator <= 0:
        raise ValueError("ratio denominator must be positive")
    with localcontext() as context:
        context.prec = 80
        threshold_amount = (denominator * threshold).quantize(
            _NUMERIC_SCALE, rounding=ROUND_DOWN
        )
    return amount >= threshold_amount


def _finish(digest: str, reasons: set[str]) -> RiskDecision:
    if any(code not in REASON_PRIORITY for code in reasons):
        reasons = {"INPUT_SCHEMA_INVALID"}
    if not reasons:
        reasons = {"RISK_ALLOWED"}
    ordered = tuple(sorted(reasons, key=lambda code: (REASON_PRIORITY[code], code)))
    verdict = (
        "ERROR"
        if any(REASON_PRIORITY[code] == 10 for code in ordered)
        else "ALLOWED"
        if ordered == ("RISK_ALLOWED",)
        else "DENIED"
    )
    decision_payload = {
        "decision_schema_version": DECISION_VERSION,
        "risk_input_digest": digest,
        "verdict": verdict,
        "ordered_reason_codes": list(ordered),
    }
    return RiskDecision(
        decision_schema_version=DECISION_VERSION,
        risk_input_digest=digest,
        verdict=verdict,
        ordered_reason_codes=ordered,
        primary_reason_code=ordered[0],
        decision_hash=canonical_hash(decision_payload),
    )


def evaluate_risk(risk_input: object) -> RiskDecision:
    """Evaluate one complete immutable Risk input without performing IO."""
    digest, canonical = _input_digest(risk_input)
    reasons: set[str] = set()
    root = _object(risk_input)
    if root is None or not canonical:
        return _finish(digest, {"INPUT_SCHEMA_INVALID"})

    missing = _ROOT_FIELDS - set(root)
    if "policy" in missing:
        reasons.add("RISK_POLICY_MISSING")
        missing.remove("policy")
    if missing or set(root) - _ROOT_FIELDS:
        reasons.add("INPUT_SCHEMA_INVALID")
    if reasons:
        return _finish(digest, reasons)
    if root["risk_input_schema_version"] != INPUT_VERSION or root["namespace"] != "test":
        reasons.add("INPUT_SCHEMA_INVALID")

    proposal = _object(root["proposal"])
    portfolio = _object(root["portfolio"])
    data = _object(root["data"])
    preview = _object(root["order_preview"])
    policy = _object(root["policy"])
    kill = _object(root["kill_switch"])
    reconciliation = _object(root["reconciliation"])
    clock = _object(root["decision_clock"])
    duplicate = _object(root["duplicate"])
    exposure_snapshot = _object(root["exposure_snapshot"])
    if any(
        item is None
        for item in (
            proposal,
            portfolio,
            data,
            preview,
            policy,
            kill,
            reconciliation,
            clock,
            duplicate,
            exposure_snapshot,
        )
    ):
        return _finish(digest, {"INPUT_SCHEMA_INVALID"})
    assert proposal is not None
    assert portfolio is not None
    assert data is not None
    assert preview is not None
    assert policy is not None
    assert kill is not None
    assert reconciliation is not None
    assert clock is not None
    assert duplicate is not None
    assert exposure_snapshot is not None

    shapes = (
        (proposal, {"fixture_contract", "schema_version", "payload", "proposal_hash"}),
        (
            portfolio,
            {
                "snapshot_id",
                "snapshot_hash",
                "available_quote",
                "held_quote",
                "fee_liabilities",
                "positions",
                "fifo_lots",
                "realized_pnl_24h",
                "high_water_equity",
                "open_orders",
            },
        ),
        (
            data,
            {
                "evidence_id",
                "evidence_hash",
                "as_of",
                "knowledge_cutoff",
                "freshness",
                "quality",
                "future_contamination",
                "watermark_complete",
            },
        ),
        (
            preview,
            {
                "symbol",
                "side",
                "order_type",
                "time_in_force",
                "quantity",
                "limit_price",
                "worst_case_fee",
                "worst_case_hold",
                "worst_case_notional",
                "best_bid",
                "best_ask",
                "expected_slippage_inputs",
                "paper_order_preview_hash",
            },
        ),
        (policy, {"version", "calculators", "limits", "policy_hash"}),
        (kill, {"active", "version", "event_id"}),
        (reconciliation, {"checkpoint_id", "checkpoint_hash", "health", "mismatch_codes"}),
        (clock, {"decision_as_of", "source", "version"}),
        (duplicate, {"key", "window_seconds", "proposal_seen", "order_intent_seen"}),
        (exposure_snapshot, {"snapshot_id", "snapshot_hash", "open_order_count"}),
    )
    if any(not _exact_fields(item, fields) for item, fields in shapes):
        return _finish(digest, {"INPUT_SCHEMA_INVALID"})

    proposal_payload = _object(proposal["payload"])
    if (
        proposal["fixture_contract"] != PROPOSAL_FIXTURE_CONTRACT
        or proposal["schema_version"] != "woozoo.trade-proposal/v1"
        or proposal_payload is None
        or not _exact_fields(
            proposal_payload, {"schema_version", "proposal_id", "symbol", "side"}
        )
        or proposal_payload["schema_version"] != "woozoo.trade-proposal/v1"
    ):
        reasons.add("INPUT_SCHEMA_INVALID")
    elif not _valid_hash(proposal["proposal_hash"]) or canonical_hash(
        proposal["payload"]
    ) != proposal["proposal_hash"]:
        reasons.add("PROPOSAL_HASH_MISMATCH")

    portfolio_body = {key: value for key, value in portfolio.items() if key != "snapshot_hash"}
    if not _valid_hash(portfolio["snapshot_hash"]) or canonical_hash(portfolio_body) != portfolio[
        "snapshot_hash"
    ]:
        reasons.add("PORTFOLIO_SNAPSHOT_MISMATCH")
    preview_body = {
        key: value for key, value in preview.items() if key != "paper_order_preview_hash"
    }
    if not _valid_hash(preview["paper_order_preview_hash"]) or canonical_hash(
        preview_body
    ) != preview["paper_order_preview_hash"]:
        reasons.add("INPUT_SCHEMA_INVALID")
    policy_body = {key: value for key, value in policy.items() if key != "policy_hash"}
    if (
        not _valid_hash(policy["policy_hash"])
        or canonical_hash(policy_body) != policy["policy_hash"]
        or policy_body != APPROVED_POLICY_BODY
        or policy["policy_hash"] != APPROVED_POLICY_HASH
    ):
        reasons.add("RISK_POLICY_HASH_MISMATCH")
    reconciliation_body = {
        key: value for key, value in reconciliation.items() if key != "checkpoint_hash"
    }
    if not _valid_hash(reconciliation["checkpoint_hash"]) or canonical_hash(
        reconciliation_body
    ) != reconciliation["checkpoint_hash"]:
        reasons.add("PORTFOLIO_SNAPSHOT_MISMATCH")
    exposure_body = {
        key: value for key, value in exposure_snapshot.items() if key != "snapshot_hash"
    }
    if not _valid_hash(exposure_snapshot["snapshot_hash"]) or canonical_hash(
        exposure_body
    ) != exposure_snapshot["snapshot_hash"]:
        reasons.add("PORTFOLIO_SNAPSHOT_MISMATCH")

    if reasons & {"INPUT_SCHEMA_INVALID", "RISK_POLICY_HASH_MISMATCH"}:
        return _finish(digest, reasons)

    calculators = _object(policy["calculators"])
    limits = _object(policy["limits"])
    positions = _object(portfolio["positions"])
    if calculators is None or limits is None or positions is None:
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})
    calculator_names = {"decimal", "fee", "valuation", "spread", "slippage"}
    if set(calculators) != calculator_names or calculators != APPROVED_CALCULATORS:
        reasons.add("INPUT_SCHEMA_INVALID")
    for calculator in calculators.values():
        item = _object(calculator)
        if (
            item is None
            or not _exact_fields(item, {"name", "version", "hash"})
            or not _valid_hash(item["hash"])
        ):
            reasons.add("DECIMAL_POLICY_INVALID")

    expected_limits = {
        "symbols",
        "order_types",
        "sides",
        "time_in_force",
        "max_order_notional_ratio",
        "symbol_exposure_ratios",
        "max_portfolio_exposure_ratio",
        "realized_loss_ratio",
        "drawdown_ratio",
        "max_spread_bps",
        "max_expected_slippage_bps",
        "duplicate_window_seconds",
    }
    if not _exact_fields(limits, expected_limits):
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})
    if limits != APPROVED_LIMITS:
        return _finish(digest, reasons | {"RISK_POLICY_HASH_MISMATCH"})
    allowed_symbols = _string_tuple(limits["symbols"])
    allowed_order_types = _string_tuple(limits["order_types"])
    allowed_sides = _string_tuple(limits["sides"])
    allowed_tifs = _string_tuple(limits["time_in_force"])
    if any(
        value is None
        for value in (allowed_symbols, allowed_order_types, allowed_sides, allowed_tifs)
    ):
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})
    assert allowed_symbols is not None
    assert allowed_order_types is not None
    assert allowed_sides is not None
    assert allowed_tifs is not None

    try:
        available_quote = _decimal(portfolio["available_quote"])
        held_quote = _decimal(portfolio["held_quote"])
        fee_liabilities = _decimal(portfolio["fee_liabilities"])
        realized_pnl = _decimal(portfolio["realized_pnl_24h"], signed=True)
        high_water = _decimal(portfolio["high_water_equity"])
        quantity = _decimal(preview["quantity"])
        limit_price = _decimal(preview["limit_price"])
        worst_fee = _decimal(preview["worst_case_fee"])
        worst_hold = _decimal(preview["worst_case_hold"])
        worst_notional = _decimal(preview["worst_case_notional"])
        best_bid = _decimal(preview["best_bid"])
        best_ask = _decimal(preview["best_ask"])
        max_order_ratio = _decimal(limits["max_order_notional_ratio"])
        max_portfolio_ratio = _decimal(limits["max_portfolio_exposure_ratio"])
        max_loss_ratio = _decimal(limits["realized_loss_ratio"])
        max_drawdown_ratio = _decimal(limits["drawdown_ratio"])
        max_spread_bps = _decimal(limits["max_spread_bps"])
        max_slippage_bps = _decimal(limits["max_expected_slippage_bps"])
    except (KeyError, ValueError):
        return _finish(digest, reasons | {"DECIMAL_POLICY_INVALID"})

    symbol = preview["symbol"]
    side = preview["side"]
    if (
        proposal_payload is not None
        and (proposal_payload["symbol"] != symbol or proposal_payload["side"] != side)
    ):
        reasons.add("PROPOSAL_HASH_MISMATCH")
    if not isinstance(symbol, str) or symbol not in allowed_symbols:
        reasons.add("SYMBOL_NOT_ALLOWED")
    if preview["order_type"] != "LIMIT" or preview["order_type"] not in allowed_order_types:
        reasons.add("ORDER_TYPE_NOT_LIMIT")
    if side not in ("BUY", "SELL") or side not in allowed_sides:
        reasons.add("SIDE_NOT_LONG_CASH")
    if preview["time_in_force"] not in allowed_tifs:
        reasons.add("TIF_NOT_ALLOWED")
    if quantity <= 0 or limit_price <= 0:
        reasons.add("INVALID_PRICE_OR_QTY")

    if not isinstance(kill["active"], bool) or not isinstance(kill["version"], int):
        reasons.add("INPUT_SCHEMA_INVALID")
    elif kill["active"]:
        reasons.add("KILL_SWITCH_ACTIVE")

    if not data["evidence_id"] or not _valid_hash(data["evidence_hash"]):
        reasons.add("EVIDENCE_MISSING")
    if data["quality"] != "HEALTHY":
        reasons.add("DATA_INVALID")
    if data["freshness"] != "FRESH":
        reasons.add("DATA_STALE")
    if data["future_contamination"] is True:
        reasons.add("FUTURE_CONTAMINATION")
    if data["watermark_complete"] is not True:
        reasons.add("WATERMARK_INCOMPLETE")
    if not all(isinstance(clock[field], str) and clock[field] for field in clock):
        reasons.add("INPUT_SCHEMA_INVALID")
    else:
        try:
            decision_as_of = datetime.fromisoformat(str(clock["decision_as_of"]).replace("Z", "+00:00"))
            as_of = datetime.fromisoformat(str(data["as_of"]).replace("Z", "+00:00"))
            cutoff = datetime.fromisoformat(str(data["knowledge_cutoff"]).replace("Z", "+00:00"))
            if any(value.tzinfo is None for value in (decision_as_of, as_of, cutoff)):
                raise ValueError("timezone required")
            if as_of > decision_as_of or cutoff > decision_as_of:
                reasons.add("FUTURE_CONTAMINATION")
        except ValueError:
            reasons.add("INPUT_SCHEMA_INVALID")

    mismatch_codes = reconciliation["mismatch_codes"]
    if reconciliation["health"] != "HEALTHY":
        reasons.add("RECONCILIATION_UNHEALTHY")
    if not isinstance(mismatch_codes, list) or not all(
        isinstance(code, str) for code in mismatch_codes
    ):
        reasons.add("INPUT_SCHEMA_INVALID")
        mismatch_codes = []
    if "LEDGER_IMBALANCE" in mismatch_codes:
        reasons.add("LEDGER_IMBALANCE")
    if "PORTFOLIO_SNAPSHOT_MISMATCH" in mismatch_codes:
        reasons.add("PORTFOLIO_SNAPSHOT_MISMATCH")

    if duplicate["proposal_seen"] is True:
        reasons.add("DUPLICATE_PROPOSAL")
    if duplicate["order_intent_seen"] is True:
        reasons.add("DUPLICATE_ORDER_INTENT")
    if (
        not isinstance(duplicate["window_seconds"], int)
        or duplicate["window_seconds"] != limits["duplicate_window_seconds"]
        or not isinstance(duplicate["key"], str)
    ):
        reasons.add("INPUT_SCHEMA_INVALID")

    position_exposures: dict[str, Decimal] = {}
    base_quantities: dict[str, Decimal] = {}
    try:
        if set(positions) != {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("invalid position inventory")
        for position_symbol in ("BTCUSDT", "ETHUSDT"):
            position = _object(positions[position_symbol])
            if position is None or not _exact_fields(position, {"available", "held", "midpoint"}):
                raise ValueError("invalid position")
            base_quantity = _decimal(position["available"]) + _decimal(position["held"])
            midpoint = _decimal(position["midpoint"])
            if midpoint <= 0:
                raise ValueError("invalid midpoint")
            base_quantities[position_symbol] = base_quantity
            position_exposures[position_symbol] = _product(base_quantity, midpoint)
    except (KeyError, ValueError):
        return _finish(digest, reasons | {"EQUITY_INVALID"})

    equity = _sum(
        [available_quote, held_quote, *position_exposures.values(), -fee_liabilities]
    )
    if equity <= 0:
        return _finish(digest, reasons | {"EQUITY_INVALID"})

    principal = _product(quantity, limit_price)
    calculated_notional = principal + worst_fee
    expected_hold = calculated_notional if side == "BUY" else quantity
    if calculated_notional != worst_notional or expected_hold != worst_hold:
        reasons.add("INPUT_SCHEMA_INVALID")
    if side == "BUY":
        if available_quote < worst_hold:
            reasons.add("INSUFFICIENT_AVAILABLE_BALANCE")
        if available_quote >= principal and available_quote < calculated_notional:
            reasons.add("FEE_RESERVE_INSUFFICIENT")
    elif side == "SELL" and isinstance(symbol, str):
        if base_quantities.get(symbol, Decimal(0)) < quantity:
            reasons.add("SELL_EXCEEDS_POSITION")

    if best_bid <= 0 or best_ask <= 0 or best_bid > best_ask:
        reasons.add("EXECUTION_QUALITY_UNKNOWN")
    else:
        midpoint = (best_ask + best_bid) / Decimal(2)
        spread_bps = _ratio(best_ask - best_bid, midpoint) * Decimal(10000)
        if spread_bps > max_spread_bps:
            reasons.add("SPREAD_LIMIT_EXCEEDED")
        raw_slippage = (
            _ratio(limit_price - best_ask, best_ask)
            if side == "BUY"
            else _ratio(best_bid - limit_price, best_bid)
        )
        slippage_bps = max(Decimal(0), raw_slippage) * Decimal(10000)
        if slippage_bps > max_slippage_bps:
            reasons.add("EXPECTED_SLIPPAGE_LIMIT_EXCEEDED")

    if _ratio(calculated_notional, equity) > max_order_ratio:
        reasons.add("ORDER_NOTIONAL_LIMIT_EXCEEDED")

    open_orders = portfolio["open_orders"]
    fifo_lots = portfolio["fifo_lots"]
    if not isinstance(open_orders, list) or not isinstance(fifo_lots, list):
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})
    try:
        for lot_raw in fifo_lots:
            lot = _object(lot_raw)
            if lot is None or not _exact_fields(
                lot, {"lot_id", "symbol", "remaining_quantity", "quote_basis", "source_fill_id"}
            ):
                raise ValueError("invalid FIFO lot")
            if lot["symbol"] not in ("BTCUSDT", "ETHUSDT"):
                raise ValueError("invalid FIFO lot symbol")
            _decimal(lot["remaining_quantity"])
            _decimal(lot["quote_basis"])
    except ValueError:
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})
    if exposure_snapshot["open_order_count"] != len(open_orders):
        reasons.add("PORTFOLIO_SNAPSHOT_MISMATCH")
    slippage_inputs = _object(preview["expected_slippage_inputs"])
    if slippage_inputs != {"method": "limit-vs-book-v1"}:
        reasons.add("INPUT_SCHEMA_INVALID")
    open_buy_commitments = {"BTCUSDT": Decimal(0), "ETHUSDT": Decimal(0)}
    try:
        for open_order_raw in open_orders:
            open_order = _object(open_order_raw)
            if open_order is None or not _exact_fields(
                open_order,
                {
                    "client_order_id",
                    "symbol",
                    "side",
                    "remaining_quantity",
                    "limit_price",
                    "remaining_worst_case_quote_fee",
                    "status",
                    "accepted_broker_seq",
                },
            ):
                raise ValueError("invalid open order")
            if open_order["side"] == "BUY" and open_order["status"] in (
                "OPEN",
                "PARTIALLY_FILLED",
            ):
                open_symbol = open_order["symbol"]
                if open_symbol not in open_buy_commitments:
                    raise ValueError("invalid open-order symbol")
                commitment = _product(
                    _decimal(open_order["remaining_quantity"]),
                    _decimal(open_order["limit_price"]),
                ) + _decimal(open_order["remaining_worst_case_quote_fee"])
                open_buy_commitments[open_symbol] += commitment
    except (KeyError, ValueError):
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})

    symbol_exposure_after: dict[str, Decimal] = {}
    ratio_map = _object(limits["symbol_exposure_ratios"])
    if ratio_map is None:
        return _finish(digest, reasons | {"INPUT_SCHEMA_INVALID"})
    try:
        for position_symbol in ("BTCUSDT", "ETHUSDT"):
            candidate = calculated_notional if side == "BUY" and symbol == position_symbol else 0
            exposure = position_exposures[position_symbol] + open_buy_commitments[position_symbol]
            exposure += candidate
            symbol_exposure_after[position_symbol] = exposure
            if _ratio(exposure, equity) > _decimal(ratio_map[position_symbol]):
                reasons.add("SYMBOL_EXPOSURE_LIMIT_EXCEEDED")
    except (KeyError, ValueError):
        return _finish(digest, reasons | {"DECIMAL_POLICY_INVALID"})
    if _ratio(_sum(list(symbol_exposure_after.values())), equity) > max_portfolio_ratio:
        reasons.add("PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED")

    realized_loss = max(Decimal(0), -realized_pnl)
    if _at_or_above_ratio(realized_loss, equity, max_loss_ratio):
        reasons.add("REALIZED_LOSS_LIMIT_EXCEEDED")
    if high_water <= 0:
        reasons.add("EQUITY_INVALID")
    else:
        drawdown_amount = max(Decimal(0), high_water - equity)
        if _at_or_above_ratio(drawdown_amount, high_water, max_drawdown_ratio):
            reasons.add("DRAWDOWN_LIMIT_EXCEEDED")

    return _finish(digest, reasons)
