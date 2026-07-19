from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from risk_engine import canonical_hash, evaluate_risk


def risk_input() -> dict[str, object]:
    proposal_payload = {
        "schema_version": "woozoo.trade-proposal/v1",
        "proposal_id": "fixture-proposal-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
    }
    portfolio = {
        "snapshot_id": "portfolio-1",
        "available_quote": "10000",
        "held_quote": "0",
        "fee_liabilities": "0",
        "positions": {
            "BTCUSDT": {"available": "0", "held": "0", "midpoint": "100"},
            "ETHUSDT": {"available": "0", "held": "0", "midpoint": "50"},
        },
        "fifo_lots": [],
        "realized_pnl_24h": "0",
        "high_water_equity": "10000",
        "open_orders": [],
    }
    preview = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "quantity": "0.1",
        "limit_price": "100",
        "worst_case_fee": "0.01",
        "worst_case_hold": "10.01",
        "worst_case_notional": "10.01",
        "best_bid": "99.99",
        "best_ask": "100",
        "expected_slippage_inputs": {"method": "limit-vs-book-v1"},
    }
    policy = {
        "version": "woozoo.risk-policy/v1",
        "calculators": {
            "decimal": {"name": "numeric-38-18", "version": "1", "hash": "1" * 64},
            "fee": {"name": "quote-fee", "version": "1", "hash": "2" * 64},
            "valuation": {"name": "midpoint", "version": "1", "hash": "3" * 64},
            "spread": {"name": "midpoint-spread-bps", "version": "1", "hash": "4" * 64},
            "slippage": {"name": "limit-vs-book-bps", "version": "1", "hash": "5" * 64},
        },
        "limits": {
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
        },
    }
    exposure = {"snapshot_id": "exposure-1", "open_order_count": 0}
    reconciliation = {
        "checkpoint_id": "recon-1",
        "health": "HEALTHY",
        "mismatch_codes": [],
    }
    return {
        "risk_input_schema_version": "woozoo.risk-input/v1",
        "namespace": "test",
        "proposal": {
            "fixture_contract": "p6-trade-proposal-consumer/v1",
            "schema_version": "woozoo.trade-proposal/v1",
            "payload": proposal_payload,
            "proposal_hash": canonical_hash(proposal_payload),
        },
        "portfolio": {**portfolio, "snapshot_hash": canonical_hash(portfolio)},
        "data": {
            "evidence_id": "evidence-1",
            "evidence_hash": "a" * 64,
            "as_of": "2026-07-19T00:00:00Z",
            "knowledge_cutoff": "2026-07-19T00:00:00Z",
            "freshness": "FRESH",
            "quality": "HEALTHY",
            "future_contamination": False,
            "watermark_complete": True,
        },
        "order_preview": {**preview, "paper_order_preview_hash": canonical_hash(preview)},
        "policy": {**policy, "policy_hash": canonical_hash(policy)},
        "kill_switch": {"active": False, "version": 0, "event_id": None},
        "reconciliation": {
            **reconciliation,
            "checkpoint_hash": canonical_hash(reconciliation),
        },
        "decision_clock": {
            "decision_as_of": "2026-07-19T00:00:00Z",
            "source": "fixture-clock",
            "version": "1",
        },
        "duplicate": {
            "key": "BTCUSDT:BUY",
            "window_seconds": 900,
            "proposal_seen": False,
            "order_intent_seen": False,
        },
        "exposure_snapshot": {**exposure, "snapshot_hash": canonical_hash(exposure)},
    }


def rehash_section(payload: dict[str, object], section_name: str, hash_name: str) -> None:
    section = payload[section_name]
    assert isinstance(section, dict)
    section[hash_name] = canonical_hash(
        {key: value for key, value in section.items() if key != hash_name}
    )


def set_preview(payload: dict[str, object], **changes: object) -> None:
    preview = payload["order_preview"]
    assert isinstance(preview, dict)
    preview.update(changes)
    rehash_section(payload, "order_preview", "paper_order_preview_hash")


def add_open_buy_commitment(payload: dict[str, object], symbol: str, quote: str) -> None:
    portfolio = payload["portfolio"]
    exposure = payload["exposure_snapshot"]
    assert isinstance(portfolio, dict) and isinstance(exposure, dict)
    quantity = Decimal(quote) / Decimal(100)
    portfolio["open_orders"] = [
        {
            "client_order_id": f"open-{symbol.lower()}",
            "symbol": symbol,
            "side": "BUY",
            "remaining_quantity": format(quantity, "f"),
            "limit_price": "100",
            "remaining_worst_case_quote_fee": "0",
            "status": "OPEN",
            "accepted_broker_seq": 1,
        }
    ]
    exposure["open_order_count"] = 1
    rehash_section(payload, "portfolio", "snapshot_hash")
    rehash_section(payload, "exposure_snapshot", "snapshot_hash")


def test_risk_001_same_complete_input_has_same_allowed_decision() -> None:
    payload = risk_input()
    first = evaluate_risk(payload)
    second = evaluate_risk(deepcopy(payload))

    assert first == second
    assert first.verdict == "ALLOWED"
    assert first.ordered_reason_codes == ("RISK_ALLOWED",)
    assert len(first.risk_input_digest) == 64
    assert len(first.decision_hash) == 64


def test_risk_001_every_bound_section_mutation_changes_digest() -> None:
    baseline = risk_input()
    baseline_digest = evaluate_risk(baseline).risk_input_digest
    mutations: tuple[tuple[str, str, object], ...] = (
        ("proposal", "proposal_hash", "0" * 64),
        ("portfolio", "snapshot_id", "portfolio-2"),
        ("data", "evidence_hash", "b" * 64),
        ("order_preview", "best_bid", "99.98"),
        ("policy", "policy_hash", "0" * 64),
        ("kill_switch", "version", 1),
        ("reconciliation", "checkpoint_id", "recon-2"),
        ("decision_clock", "version", "2"),
        ("duplicate", "key", "BTCUSDT:BUY:2"),
        ("exposure_snapshot", "snapshot_id", "exposure-2"),
    )
    for section, field, replacement in mutations:
        candidate = deepcopy(baseline)
        candidate[section][field] = replacement  # type: ignore[index]
        assert evaluate_risk(candidate).risk_input_digest != baseline_digest


def test_risk_002_closed_reason_precedence_collects_all_applicable_reasons() -> None:
    payload = risk_input()
    payload["kill_switch"] = {"active": True, "version": 4, "event_id": "kill-4"}
    payload["data"]["freshness"] = "STALE"  # type: ignore[index]
    payload["duplicate"]["proposal_seen"] = True  # type: ignore[index]
    payload["order_preview"]["symbol"] = "DOGEUSDT"  # type: ignore[index]
    preview = payload["order_preview"]  # type: ignore[assignment]
    preview["paper_order_preview_hash"] = canonical_hash(
        {key: value for key, value in preview.items() if key != "paper_order_preview_hash"}
    )

    decision = evaluate_risk(payload)

    assert decision.verdict == "DENIED"
    assert decision.ordered_reason_codes == (
        "KILL_SWITCH_ACTIVE",
        "DATA_STALE",
        "DUPLICATE_PROPOSAL",
        "PROPOSAL_HASH_MISMATCH",
        "SYMBOL_NOT_ALLOWED",
    )
    assert decision.primary_reason_code == "KILL_SWITCH_ACTIVE"


def test_invalid_or_non_fixture_input_fails_closed_without_approval_dependency() -> None:
    missing = risk_input()
    del missing["portfolio"]
    assert evaluate_risk(missing).ordered_reason_codes == ("INPUT_SCHEMA_INVALID",)

    production = risk_input()
    production["namespace"] = "production"
    assert evaluate_risk(production).ordered_reason_codes == ("INPUT_SCHEMA_INVALID",)

    active = risk_input()
    active["kill_switch"] = {"active": True, "version": 1, "event_id": "kill-1"}
    assert evaluate_risk(active).ordered_reason_codes == ("KILL_SWITCH_ACTIVE",)


def test_nested_unknowns_policy_rebinding_and_cross_snapshot_mismatch_fail_closed() -> None:
    nested = risk_input()
    nested["proposal"]["payload"]["unexpected"] = True  # type: ignore[index]
    nested["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
        nested["proposal"]["payload"]  # type: ignore[index]
    )
    assert evaluate_risk(nested).ordered_reason_codes == ("INPUT_SCHEMA_INVALID",)

    policy = risk_input()
    policy["policy"]["limits"]["max_order_notional_ratio"] = "1"  # type: ignore[index]
    policy["policy"]["policy_hash"] = canonical_hash(  # type: ignore[index]
        {key: value for key, value in policy["policy"].items() if key != "policy_hash"}  # type: ignore[union-attr]
    )
    assert evaluate_risk(policy).ordered_reason_codes == ("RISK_POLICY_HASH_MISMATCH",)

    count = risk_input()
    count["exposure_snapshot"]["open_order_count"] = 1  # type: ignore[index]
    count["exposure_snapshot"]["snapshot_hash"] = canonical_hash(  # type: ignore[index]
        {
            key: value
            for key, value in count["exposure_snapshot"].items()  # type: ignore[union-attr]
            if key != "snapshot_hash"
        }
    )
    assert "PORTFOLIO_SNAPSHOT_MISMATCH" in evaluate_risk(count).ordered_reason_codes


@pytest.mark.parametrize("case", ["numeric_proposal_id", "negative_kill_version"])
def test_complete_input_schema_rejects_nested_type_and_range_counterexamples(case: str) -> None:
    payload = risk_input()
    if case == "numeric_proposal_id":
        payload["proposal"]["payload"]["proposal_id"] = 7  # type: ignore[index]
        payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
            payload["proposal"]["payload"]  # type: ignore[index]
        )
    else:
        payload["kill_switch"]["version"] = -1  # type: ignore[index]

    decision = evaluate_risk(payload)

    assert decision.verdict == "ERROR"
    assert decision.ordered_reason_codes == ("INPUT_SCHEMA_INVALID",)


def test_decimal_thresholds_use_unrounded_values_and_documented_comparators() -> None:
    payload = risk_input()
    preview = payload["order_preview"]  # type: ignore[assignment]
    preview["quantity"] = "0.25"
    preview["worst_case_fee"] = "0"
    preview["worst_case_hold"] = "25"
    preview["worst_case_notional"] = "25"
    unhashed = {key: value for key, value in preview.items() if key != "paper_order_preview_hash"}
    preview["paper_order_preview_hash"] = canonical_hash(unhashed)
    assert "ORDER_NOTIONAL_LIMIT_EXCEEDED" not in evaluate_risk(payload).ordered_reason_codes

    preview["quantity"] = "0.250000000000000001"
    preview["worst_case_hold"] = "25.000000000000000100"
    preview["worst_case_notional"] = "25.000000000000000100"
    unhashed = {key: value for key, value in preview.items() if key != "paper_order_preview_hash"}
    preview["paper_order_preview_hash"] = canonical_hash(unhashed)
    assert "ORDER_NOTIONAL_LIMIT_EXCEEDED" in evaluate_risk(payload).ordered_reason_codes

    loss = risk_input()
    loss["portfolio"]["realized_pnl_24h"] = "-100"  # type: ignore[index]
    loss["portfolio"]["snapshot_hash"] = canonical_hash(  # type: ignore[index]
        {key: value for key, value in loss["portfolio"].items() if key != "snapshot_hash"}  # type: ignore[union-attr]
    )
    assert "REALIZED_LOSS_LIMIT_EXCEEDED" in evaluate_risk(loss).ordered_reason_codes

    drawdown = risk_input()
    drawdown["portfolio"]["available_quote"] = "9500"  # type: ignore[index]
    drawdown["portfolio"]["snapshot_hash"] = canonical_hash(  # type: ignore[index]
        {key: value for key, value in drawdown["portfolio"].items() if key != "snapshot_hash"}  # type: ignore[union-attr]
    )
    assert "DRAWDOWN_LIMIT_EXCEEDED" in evaluate_risk(drawdown).ordered_reason_codes


RISK_BOUNDARY_CASES = [
    ("order_notional_below", "ORDER_NOTIONAL_LIMIT_EXCEEDED", False),
    ("order_notional_equal", "ORDER_NOTIONAL_LIMIT_EXCEEDED", False),
    ("order_notional_above", "ORDER_NOTIONAL_LIMIT_EXCEEDED", True),
    ("sell_order_notional_below", "ORDER_NOTIONAL_LIMIT_EXCEEDED", False),
    ("sell_order_notional_equal", "ORDER_NOTIONAL_LIMIT_EXCEEDED", False),
    ("sell_order_notional_above", "ORDER_NOTIONAL_LIMIT_EXCEEDED", True),
    ("realized_loss_below", "REALIZED_LOSS_LIMIT_EXCEEDED", False),
    ("realized_loss_equal", "REALIZED_LOSS_LIMIT_EXCEEDED", True),
    ("realized_loss_above", "REALIZED_LOSS_LIMIT_EXCEEDED", True),
    ("drawdown_below", "DRAWDOWN_LIMIT_EXCEEDED", False),
    ("drawdown_equal", "DRAWDOWN_LIMIT_EXCEEDED", True),
    ("drawdown_above", "DRAWDOWN_LIMIT_EXCEEDED", True),
    ("spread_below", "SPREAD_LIMIT_EXCEEDED", False),
    ("spread_equal", "SPREAD_LIMIT_EXCEEDED", False),
    ("spread_above", "SPREAD_LIMIT_EXCEEDED", True),
    ("slippage_below", "EXPECTED_SLIPPAGE_LIMIT_EXCEEDED", False),
    ("slippage_equal", "EXPECTED_SLIPPAGE_LIMIT_EXCEEDED", False),
    ("slippage_above", "EXPECTED_SLIPPAGE_LIMIT_EXCEEDED", True),
    ("btc_exposure_below", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("btc_exposure_equal", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("btc_exposure_above", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", True),
    ("eth_exposure_below", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("eth_exposure_equal", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("eth_exposure_above", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", True),
    ("portfolio_exposure_below", "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", False),
    ("portfolio_exposure_equal", "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", False),
    ("portfolio_exposure_above", "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", True),
    ("sell_btc_exposure_below", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("sell_btc_exposure_equal", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("sell_btc_exposure_above", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", True),
    ("sell_eth_exposure_below", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("sell_eth_exposure_equal", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("sell_eth_exposure_above", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", True),
    ("sell_portfolio_exposure_below", "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", False),
    ("sell_portfolio_exposure_equal", "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", False),
    ("sell_portfolio_exposure_above", "PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", True),
    ("sell_reduces_long", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED", False),
    ("evidence_missing", "EVIDENCE_MISSING", True),
    ("data_stale", "DATA_STALE", True),
    ("future_contamination", "FUTURE_CONTAMINATION", True),
    ("watermark_incomplete", "WATERMARK_INCOMPLETE", True),
    ("data_invalid", "DATA_INVALID", True),
    ("expired", "DATA_STALE", True),
    ("duplicate_proposal", "DUPLICATE_PROPOSAL", True),
    ("duplicate_order_intent", "DUPLICATE_ORDER_INTENT", True),
    ("equity_invalid", "EQUITY_INVALID", True),
    ("bid_missing", "EXECUTION_QUALITY_UNKNOWN", True),
    ("ask_missing", "EXECUTION_QUALITY_UNKNOWN", True),
    ("bid_zero", "EXECUTION_QUALITY_UNKNOWN", True),
    ("ask_zero", "EXECUTION_QUALITY_UNKNOWN", True),
    ("order_type_not_limit", "ORDER_TYPE_NOT_LIMIT", True),
    ("side_not_long_cash", "SIDE_NOT_LONG_CASH", True),
    ("tif_not_allowed", "TIF_NOT_ALLOWED", True),
    ("invalid_price_or_qty", "INVALID_PRICE_OR_QTY", True),
    ("insufficient_available_balance", "INSUFFICIENT_AVAILABLE_BALANCE", True),
    ("fee_reserve_insufficient", "FEE_RESERVE_INSUFFICIENT", True),
    ("sell_exceeds_position", "SELL_EXCEEDS_POSITION", True),
    ("ledger_mismatch", "LEDGER_IMBALANCE", True),
    ("invalid_open_order_state", "INPUT_SCHEMA_INVALID", True),
    ("loss_scale_edge_below", "REALIZED_LOSS_LIMIT_EXCEEDED", False),
]


@pytest.mark.parametrize(
    ("case", "reason", "present"),
    RISK_BOUNDARY_CASES,
    ids=[case for case, _, _ in RISK_BOUNDARY_CASES],
)
def test_risk_002_complete_boundary_matrix(case: str, reason: str, present: bool) -> None:
    payload = risk_input()
    if case.startswith("order_notional_") or case.startswith("sell_order_notional_"):
        quantity = {"below": "0.2499", "equal": "0.25", "above": "0.2501"}[case.rsplit("_", 1)[1]]
        notional = format(Decimal(quantity) * Decimal(100), "f")
        if case.startswith("sell_"):
            payload["proposal"]["payload"]["side"] = "SELL"  # type: ignore[index]
            payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
                payload["proposal"]["payload"]  # type: ignore[index]
            )
            payload["portfolio"]["positions"]["BTCUSDT"]["available"] = "1"  # type: ignore[index]
            payload["portfolio"]["available_quote"] = "9900"  # type: ignore[index]
            rehash_section(payload, "portfolio", "snapshot_hash")
        set_preview(
            payload,
            side="SELL" if case.startswith("sell_") else "BUY",
            quantity=quantity,
            worst_case_fee="0",
            worst_case_hold=quantity if case.startswith("sell_") else notional,
            worst_case_notional=notional,
        )
    elif case.startswith("realized_loss_"):
        loss = {"below": "-99.99", "equal": "-100", "above": "-100.01"}[case.rsplit("_", 1)[1]]
        payload["portfolio"]["realized_pnl_24h"] = loss  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
    elif case.startswith("drawdown_"):
        equity = {"below": "9500.01", "equal": "9500", "above": "9499.99"}[case.rsplit("_", 1)[1]]
        payload["portfolio"]["available_quote"] = equity  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
    elif case.startswith("spread_"):
        bid, ask = {
            "below": ("99.876", "100.124"),
            "equal": ("99.875", "100.125"),
            "above": ("99.8749", "100.1251"),
        }[case.rsplit("_", 1)[1]]
        set_preview(payload, best_bid=bid, best_ask=ask)
    elif case.startswith("slippage_"):
        limit = {"below": "100.2499", "equal": "100.25", "above": "100.2501"}[
            case.rsplit("_", 1)[1]
        ]
        notional = Decimal("0.1") * Decimal(limit) + Decimal("0.01")
        set_preview(
            payload,
            limit_price=limit,
            worst_case_hold=format(notional, "f"),
            worst_case_notional=format(notional, "f"),
        )
    elif case.startswith("btc_exposure_"):
        quote = {"below": "1489.98", "equal": "1489.99", "above": "1490"}[case.rsplit("_", 1)[1]]
        add_open_buy_commitment(payload, "BTCUSDT", quote)
    elif case.startswith("eth_exposure_"):
        quote = {"below": "989.98", "equal": "989.99", "above": "990"}[case.rsplit("_", 1)[1]]
        proposal_payload = payload["proposal"]["payload"]  # type: ignore[index]
        proposal_payload["symbol"] = "ETHUSDT"
        payload["proposal"]["proposal_hash"] = canonical_hash(proposal_payload)  # type: ignore[index]
        set_preview(payload, symbol="ETHUSDT")
        add_open_buy_commitment(payload, "ETHUSDT", quote)
    elif case.startswith("portfolio_exposure_"):
        total = {"below": "2499.99", "equal": "2500", "above": "2500.01"}[case.rsplit("_", 1)[1]]
        btc = Decimal("1489.99")
        eth = Decimal(total) - Decimal("10.01") - btc
        add_open_buy_commitment(payload, "BTCUSDT", format(btc, "f"))
        portfolio = payload["portfolio"]
        exposure = payload["exposure_snapshot"]
        portfolio["open_orders"].append(  # type: ignore[index,union-attr]
            {
                "client_order_id": "open-ethusdt",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "remaining_quantity": format(eth / 100, "f"),
                "limit_price": "100",
                "remaining_worst_case_quote_fee": "0",
                "status": "OPEN",
                "accepted_broker_seq": 2,
            }
        )
        exposure["open_order_count"] = 2  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
        rehash_section(payload, "exposure_snapshot", "snapshot_hash")
    elif case.startswith("sell_btc_exposure_") or case.startswith("sell_eth_exposure_"):
        symbol = "BTCUSDT" if case.startswith("sell_btc") else "ETHUSDT"
        level = case.rsplit("_", 1)[1]
        exposure = {
            "BTCUSDT": {"below": "1499.99", "equal": "1500", "above": "1500.01"},
            "ETHUSDT": {"below": "999.99", "equal": "1000", "above": "1000.01"},
        }[symbol][level]
        payload["proposal"]["payload"].update({"symbol": symbol, "side": "SELL"})  # type: ignore[index]
        payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
            payload["proposal"]["payload"]  # type: ignore[index]
        )
        midpoint = Decimal(100) if symbol == "BTCUSDT" else Decimal(50)
        payload["portfolio"]["positions"][symbol]["available"] = format(  # type: ignore[index]
            Decimal(exposure) / midpoint, "f"
        )
        payload["portfolio"]["available_quote"] = format(Decimal(10000) - Decimal(exposure), "f")  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
        set_preview(payload, symbol=symbol, side="SELL", worst_case_hold="0.1")
    elif case.startswith("sell_portfolio_exposure_"):
        level = case.rsplit("_", 1)[1]
        total = {"below": "2499.99", "equal": "2500", "above": "2500.01"}[level]
        btc = Decimal("1500")
        eth = Decimal(total) - btc
        payload["proposal"]["payload"]["side"] = "SELL"  # type: ignore[index]
        payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
            payload["proposal"]["payload"]  # type: ignore[index]
        )
        payload["portfolio"]["positions"]["BTCUSDT"]["available"] = format(btc / 100, "f")  # type: ignore[index]
        payload["portfolio"]["positions"]["ETHUSDT"]["available"] = format(eth / 50, "f")  # type: ignore[index]
        payload["portfolio"]["available_quote"] = format(Decimal(10000) - Decimal(total), "f")  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
        set_preview(payload, side="SELL", worst_case_hold="0.1")
    elif case == "sell_reduces_long":
        payload["proposal"]["payload"]["side"] = "SELL"  # type: ignore[index]
        payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
            payload["proposal"]["payload"]  # type: ignore[index]
        )
        payload["portfolio"]["positions"]["BTCUSDT"]["available"] = "1"  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
        set_preview(payload, side="SELL", worst_case_hold="0.1")
    elif case == "evidence_missing":
        payload["data"]["evidence_id"] = ""  # type: ignore[index]
    elif case == "data_stale":
        payload["data"]["freshness"] = "STALE"  # type: ignore[index]
    elif case == "future_contamination":
        payload["data"]["as_of"] = "2026-07-20T00:00:00Z"  # type: ignore[index]
    elif case == "watermark_incomplete":
        payload["data"]["watermark_complete"] = False  # type: ignore[index]
    elif case == "data_invalid":
        payload["data"]["quality"] = "INVALID"  # type: ignore[index]
    elif case == "expired":
        payload["data"]["freshness"] = "STALE"  # type: ignore[index]
    elif case == "duplicate_proposal":
        payload["duplicate"]["proposal_seen"] = True  # type: ignore[index]
    elif case == "duplicate_order_intent":
        payload["duplicate"]["order_intent_seen"] = True  # type: ignore[index]
    elif case == "equity_invalid":
        payload["portfolio"]["available_quote"] = "0"  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
    elif case == "bid_missing":
        del payload["order_preview"]["best_bid"]  # type: ignore[index]
        rehash_section(payload, "order_preview", "paper_order_preview_hash")
    elif case == "ask_missing":
        del payload["order_preview"]["best_ask"]  # type: ignore[index]
        rehash_section(payload, "order_preview", "paper_order_preview_hash")
    elif case == "bid_zero":
        set_preview(payload, best_bid="0")
    elif case == "ask_zero":
        set_preview(payload, best_ask="0")
    elif case == "order_type_not_limit":
        set_preview(payload, order_type="MARKET")
    elif case == "side_not_long_cash":
        payload["proposal"]["payload"]["side"] = "SHORT"  # type: ignore[index]
        payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
            payload["proposal"]["payload"]  # type: ignore[index]
        )
        set_preview(payload, side="SHORT", worst_case_hold="0.1")
    elif case == "tif_not_allowed":
        set_preview(payload, time_in_force="IOC")
    elif case == "invalid_price_or_qty":
        set_preview(
            payload,
            quantity="0",
            worst_case_fee="0",
            worst_case_hold="0",
            worst_case_notional="0",
        )
    elif case in {"insufficient_available_balance", "fee_reserve_insufficient"}:
        available = "9.99" if case == "insufficient_available_balance" else "10"
        payload["portfolio"]["available_quote"] = available  # type: ignore[index]
        payload["portfolio"]["held_quote"] = format(Decimal(10000) - Decimal(available), "f")  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
    elif case == "sell_exceeds_position":
        payload["proposal"]["payload"]["side"] = "SELL"  # type: ignore[index]
        payload["proposal"]["proposal_hash"] = canonical_hash(  # type: ignore[index]
            payload["proposal"]["payload"]  # type: ignore[index]
        )
        set_preview(payload, side="SELL", worst_case_hold="0.1")
    elif case == "ledger_mismatch":
        payload["reconciliation"]["health"] = "FAILED"  # type: ignore[index]
        payload["reconciliation"]["mismatch_codes"] = ["LEDGER_IMBALANCE"]  # type: ignore[index]
        rehash_section(payload, "reconciliation", "checkpoint_hash")
    elif case == "invalid_open_order_state":
        add_open_buy_commitment(payload, "BTCUSDT", "1")
        payload["portfolio"]["open_orders"][0]["side"] = "HOLD"  # type: ignore[index]
        payload["portfolio"]["open_orders"][0]["status"] = "CANCELLED"  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
    elif case == "loss_scale_edge_below":
        payload["portfolio"]["available_quote"] = "10000.000000000000000001"  # type: ignore[index]
        payload["portfolio"]["realized_pnl_24h"] = "-100"  # type: ignore[index]
        rehash_section(payload, "portfolio", "snapshot_hash")
    else:
        raise AssertionError(case)

    decision = evaluate_risk(payload)
    reasons = decision.ordered_reason_codes
    assert (reason in reasons) is present
    if case == "ledger_mismatch":
        expected = ("LEDGER_IMBALANCE", "RECONCILIATION_UNHEALTHY")
    elif case == "fee_reserve_insufficient":
        expected = ("FEE_RESERVE_INSUFFICIENT", "INSUFFICIENT_AVAILABLE_BALANCE")
    elif case in {"portfolio_exposure_above", "sell_portfolio_exposure_above"}:
        expected = ("PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED", "SYMBOL_EXPOSURE_LIMIT_EXCEEDED")
    elif present:
        expected = (reason,)
    else:
        expected = ("RISK_ALLOWED",)
    assert reasons == expected
    assert decision.primary_reason_code == expected[0]
    assert decision.verdict == (
        "ERROR"
        if reason in {"EQUITY_INVALID", "INPUT_SCHEMA_INVALID"}
        else "DENIED"
        if present
        else "ALLOWED"
    )
    if case.startswith("drawdown_"):
        assert payload["kill_switch"] == {"active": False, "version": 0, "event_id": None}
