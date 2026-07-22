from __future__ import annotations

from copy import deepcopy

import pytest

from risk_engine import canonical_hash, evaluate_risk
from test_risk_engine import risk_input


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("data", "evidence_hash", "b" * 64),
        ("kill_switch", "version", 7),
        ("decision_clock", "source", "replay-clock"),
        ("duplicate", "key", "BTCUSDT:BUY:other"),
        ("exposure_snapshot", "open_order_count", 1),
    ],
)
def test_each_bound_input_mutation_changes_risk_digest(
    section: str, field: str, replacement: object
) -> None:
    original = risk_input()
    changed = deepcopy(original)
    changed[section][field] = replacement  # type: ignore[index]
    if section == "exposure_snapshot":
        snapshot = changed[section]  # type: ignore[assignment]
        snapshot["snapshot_hash"] = canonical_hash(
            {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
        )

    assert evaluate_risk(original).risk_input_digest != evaluate_risk(changed).risk_input_digest


@pytest.mark.parametrize("value", [1.0, "NaN", "Infinity", "-1", "1e2"])
def test_binary_or_non_plain_decimal_input_is_never_allowed(value: object) -> None:
    payload = risk_input()
    payload["order_preview"]["quantity"] = value  # type: ignore[index]
    assert evaluate_risk(payload).verdict == "ERROR"


def test_spread_and_directional_slippage_boundaries() -> None:
    spread = risk_input()
    preview = spread["order_preview"]  # type: ignore[assignment]
    preview["best_bid"] = "99"
    preview["best_ask"] = "101"
    preview["limit_price"] = "102"
    preview["worst_case_fee"] = "0.0102"
    preview["worst_case_hold"] = "10.2102"
    preview["worst_case_notional"] = "10.2102"
    preview["paper_order_preview_hash"] = canonical_hash(
        {key: value for key, value in preview.items() if key != "paper_order_preview_hash"}
    )
    reasons = evaluate_risk(spread).ordered_reason_codes
    assert "SPREAD_LIMIT_EXCEEDED" in reasons
    assert "EXPECTED_SLIPPAGE_LIMIT_EXCEEDED" in reasons
