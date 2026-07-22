from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import random

from testnet_execution.financial import (
    JournalLine,
    MultiCommodityLedger,
    TestnetRiskContext,
    evaluate_testnet_risk,
)


NOW = datetime(2026, 7, 22, tzinfo=UTC)


def context(**overrides: object) -> TestnetRiskContext:
    values: dict[str, object] = {
        "proposal_id": "a" * 64,
        "proposal_hash": "b" * 64,
        "evidence_id": "c" * 64,
        "evidence_digest": "d" * 64,
        "data_state_digest": "e" * 64,
        "account_binding_id": "f" * 64,
        "account_generation": 1,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.001",
        "limit_price": "60000",
        "available_quote": "1000",
        "available_base": "1",
        "symbol_rules_digest": "1" * 64,
        "ledger_snapshot_digest": "2" * 64,
        "reconciliation_checkpoint_digest": "3" * 64,
        "paper_kill_version": 0,
        "testnet_barrier_version": 1,
        "as_of": NOW,
        "knowledge_cutoff": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "data_healthy": True,
        "paper_kill_active": False,
        "testnet_barrier_active": False,
        "reconciliation_healthy": True,
        "ledger_healthy": True,
    }
    values.update(overrides)
    return TestnetRiskContext(**values)  # type: ignore[arg-type]


def test_testnet_risk_preview_is_replay_stable_and_float_free() -> None:
    first = evaluate_testnet_risk(context())
    second = evaluate_testnet_risk(context())
    assert first == second
    assert first.verdict == "ALLOWED"
    assert first.preview is not None
    assert first.preview["quantity"] == "0.001000000000000000"
    assert first.preview["limit_price"] == "60000.000000000000000000"
    assert all(not isinstance(value, float) for value in first.preview.values())


def test_every_safety_degradation_is_denied_with_a_deterministic_non_effecting_preview() -> None:
    for mutation in (
        {"data_healthy": False},
        {"paper_kill_active": True},
        {"testnet_barrier_active": True},
        {"reconciliation_healthy": False},
        {"ledger_healthy": False},
        {"available_quote": "1"},
    ):
        result = evaluate_testnet_risk(context(**mutation))
        assert result.verdict == "DENIED"
        assert result.preview is not None
    invalid = evaluate_testnet_risk(context(quantity="NaN"))
    assert invalid.verdict == "ERROR"
    assert invalid.preview is None


def test_generated_testnet_fill_journals_balance_each_commodity_and_replay_once() -> None:
    generator = random.Random(8)
    ledger = MultiCommodityLedger()
    for index in range(200):
        quantity = Decimal(generator.randrange(1, 1000)) / Decimal(100000)
        notional = quantity * Decimal(generator.randrange(1000, 70000))
        lines = (
            JournalLine("inventory:BTC", "BTC", quantity, Decimal(0)),
            JournalLine("exchange:BTC", "BTC", Decimal(0), quantity),
            JournalLine("exchange:USDT", "USDT", notional, Decimal(0)),
            JournalLine("cash:USDT", "USDT", Decimal(0), notional),
        )
        event_id = f"fill-{index}"
        assert ledger.post(event_id, lines) is True
        assert ledger.post(event_id, lines) is False
    assert len(ledger.transactions) == 200
