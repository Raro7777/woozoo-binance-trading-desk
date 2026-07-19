from decimal import Decimal

import pytest

from datetime import UTC, datetime

from paper_engine import ExecutionFixture, MarkFixture, OrderSide, PaperEngine


def fixture(number: int) -> ExecutionFixture:
    return ExecutionFixture(f"auth-{number}", f"nonce-{number}", f"{number:x}".rjust(64, "0"))


def test_fin_001_generated_fill_cancel_sequences_conserve_and_balance() -> None:
    for case in range(1, 41):
        engine = PaperEngine()
        engine.seed_balance("USDT", "10000", seed_id=f"seed-{case}")
        quantity = Decimal(case) / Decimal("100")
        order = engine.create_limit_order(
            idempotency_key=f"cmd-{case}",
            client_order_id=f"order-{case}",
            authorization=fixture(case),
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity_text=format(quantity, "f"),
            limit_price_text="1000",
        )
        for part in range(1, 6):
            engine.apply_book_observation(
                order_id=order.order_id,
                observation_id=f"book-{case}-{part}",
                best_bid_text="999",
                best_ask_text="1000",
                displayed_quantity_text=format(quantity * Decimal("2"), "f"),
            )
            if engine.orders[order.order_id].filled_quantity == quantity:
                break
        current = engine.orders[order.order_id]
        if current.filled_quantity < current.quantity:
            engine.cancel(order.order_id, cancel_id=f"cancel-{case}")
        assert engine.available["USDT"] >= 0
        assert engine.held.get("USDT", Decimal(0)) >= 0
        assert engine.position("BTC") >= 0
        assert sum(fill.quantity for fill in engine.fills.values()) <= quantity
        for journal in engine.journals.values():
            journal.assert_balanced()


def test_fin_002_insufficient_cash_or_long_inventory_has_zero_order_and_ledger_effect() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "10", seed_id="seed")
    journals_before = dict(engine.journals)
    with pytest.raises(ValueError, match="INSUFFICIENT_FUNDS"):
        engine.create_limit_order(
            idempotency_key="buy",
            client_order_id="buy",
            authorization=fixture(1),
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity_text="1",
            limit_price_text="11",
        )
    assert engine.orders == {}
    assert engine.journals == journals_before
    with pytest.raises(ValueError, match="INSUFFICIENT_FUNDS"):
        engine.create_limit_order(
            idempotency_key="sell",
            client_order_id="sell",
            authorization=fixture(2),
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            quantity_text="0.1",
            limit_price_text="100",
        )
    assert engine.orders == {}
    assert engine.available["USDT"] == Decimal("10.000000000000000000")


def test_fin_003_exact_partial_fill_fifo_and_pnl_oracle() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "200", seed_id="J-000")
    buy = engine.create_limit_order(
        idempotency_key="J-001",
        client_order_id="buy",
        authorization=fixture(1),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.01000000",
        limit_price_text="10000.000000",
    )
    for event, displayed in (("B1", "0.02000000"), ("B2", "0.01500000"), ("B3", "0.02500000")):
        engine.apply_book_observation(
            order_id=buy.order_id,
            observation_id=event,
            best_bid_text="9999",
            best_ask_text="10000",
            displayed_quantity_text=displayed,
        )
    engine.cancel(buy.order_id, cancel_id="J-005")
    sell = engine.create_limit_order(
        idempotency_key="J-006",
        client_order_id="sell",
        authorization=fixture(2),
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity_text="0.00400000",
        limit_price_text="11000.000000",
    )
    engine.apply_book_observation(
        order_id=sell.order_id,
        observation_id="S1",
        best_bid_text="11000",
        best_ask_text="11001",
        displayed_quantity_text="0.04000000",
    )
    assert engine.available["USDT"] == Decimal("183.896000000000000000")
    assert engine.held["USDT"] == 0
    assert engine.position("BTC") == Decimal("0.002000000000000000")
    assert engine.realized_pnl("BTC") == Decimal("3.916000000000000000")
    mark = MarkFixture("11000", "fixture", datetime(2026, 7, 19, tzinfo=UTC), "VALID", "mark-v1")
    unrealized = engine.unrealized_pnl("BTC", mark)
    assert unrealized.amount == Decimal("1.980000000000000000")
    assert unrealized.mark == mark
    assert engine.realized_pnl("BTC") + unrealized.amount == Decimal("5.896000000000000000")


def test_ord_001_one_observation_budget_is_shared_across_orders() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="seed")
    orders = [
        engine.create_limit_order(
            idempotency_key=f"create-{number}",
            client_order_id=f"client-{number}",
            authorization=fixture(number),
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity_text="0.1",
            limit_price_text="100",
        )
        for number in (1, 2)
    ]
    for order in orders:
        engine.apply_book_observation(
            order_id=order.order_id,
            observation_id="shared",
            best_bid_text="99",
            best_ask_text="100",
            displayed_quantity_text="1",
        )
    assert sum(fill.quantity for fill in engine.fills.values()) == Decimal("0.100000000000000000")


def test_ord_001_shared_observation_requires_canonical_order_allocation() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="canonical-seed")
    first, second = [
        engine.create_limit_order(
            idempotency_key=f"canonical-create-{number}",
            client_order_id=f"canonical-client-{number}",
            authorization=fixture(number + 10),
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity_text="0.1",
            limit_price_text="100",
        )
        for number in (1, 2)
    ]
    sequence_before = engine.broker_seq
    with pytest.raises(ValueError, match="NON_CANONICAL_OBSERVATION_ORDER"):
        engine.apply_book_observation(
            order_id=second.order_id,
            observation_id="canonical-shared",
            best_bid_text="99",
            best_ask_text="100",
            displayed_quantity_text="1",
        )
    assert engine.broker_seq == sequence_before
    assert engine.observation_budgets == {}
    engine.apply_book_observation(
        order_id=first.order_id,
        observation_id="canonical-shared",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="1",
    )
