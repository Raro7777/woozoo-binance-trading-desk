from decimal import Decimal

import pytest

from paper_engine import ExecutionFixture, OrderSide, OrderStatus, PaperEngine


def authorization(number: int = 1) -> ExecutionFixture:
    return ExecutionFixture(f"auth-{number}", f"nonce-{number}", f"{number:x}".rjust(64, "0"))


def test_fin_003_buy_sell_fifo_and_quote_fee_oracle() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    buy = engine.create_limit_order(
        idempotency_key="buy-1",
        client_order_id="buy-1",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    buy_fill = engine.apply_book_observation(
        order_id=buy.order_id,
        observation_id="book-1",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="10",
    )
    assert buy_fill is not None
    assert buy_fill.fee_amount == Decimal("0.100000000000000000")
    assert engine.position("BTC") == Decimal("1.000000000000000000")

    sell = engine.create_limit_order(
        idempotency_key="sell-1",
        client_order_id="sell-1",
        authorization=authorization(2),
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity_text="0.4",
        limit_price_text="120",
    )
    sell_fill = engine.apply_book_observation(
        order_id=sell.order_id,
        observation_id="book-2",
        best_bid_text="120",
        best_ask_text="121",
        displayed_quantity_text="4",
    )
    assert sell_fill is not None
    assert engine.position("BTC") == Decimal("0.600000000000000000")
    assert engine.available["USDT"] == Decimal("947.852000000000000000")
    assert sum(item.quote_basis for item in engine.consumptions) == Decimal("40.040000000000000000")


def test_ord_001_partial_fill_duplicate_cancel_and_terminal_monotonicity() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    order = engine.create_limit_order(
        idempotency_key="create",
        client_order_id="client",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    first = engine.apply_book_observation(
        order_id=order.order_id,
        observation_id="one",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="2",
    )
    duplicate = engine.apply_book_observation(
        order_id=order.order_id,
        observation_id="one",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="999",
    )
    assert first == duplicate
    assert engine.orders[order.order_id].filled_quantity == Decimal("0.200000000000000000")
    cancelled = engine.cancel(order.order_id, cancel_id="cancel")
    assert cancelled.status == OrderStatus.CANCELLED
    with pytest.raises(ValueError, match="TERMINAL_ORDER"):
        engine.apply_book_observation(
            order_id=order.order_id,
            observation_id="two",
            best_bid_text="99",
            best_ask_text="100",
            displayed_quantity_text="99",
        )


def test_ord_002_command_and_authorization_are_single_effect() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    fixture = authorization()
    first = engine.create_limit_order(
        idempotency_key="same",
        client_order_id="same",
        authorization=fixture,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    second = engine.create_limit_order(
        idempotency_key="same",
        client_order_id="same",
        authorization=fixture,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    assert first == second
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        engine.create_limit_order(
            idempotency_key="same",
            client_order_id="same",
            authorization=fixture,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity_text="2",
            limit_price_text="100",
        )


def test_phase_four_rejects_production_authorization() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    with pytest.raises(ValueError, match="PHASE_4_PRODUCTION_AUTHORIZATION_FORBIDDEN"):
        engine.create_limit_order(
            idempotency_key="x",
            client_order_id="x",
            authorization=ExecutionFixture("a", "n", "0" * 64, namespace="production"),
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity_text="1",
            limit_price_text="100",
        )


def test_fin_001_every_journal_balances_per_commodity() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    order = engine.create_limit_order(
        idempotency_key="create",
        client_order_id="client",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    engine.apply_book_observation(
        order_id=order.order_id,
        observation_id="book",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="10",
    )
    for journal in engine.journals.values():
        journal.assert_balanced()


def test_atom_001_failure_rolls_back_domain_ledger_and_outbox() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    before = engine.semantic_digest()
    with pytest.raises(RuntimeError, match="INJECTED_COMMIT_FAILURE"):
        engine.atomic(
            lambda: engine.create_limit_order(
                idempotency_key="create",
                client_order_id="client",
                authorization=authorization(),
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                quantity_text="1",
                limit_price_text="100",
            ),
            fail_after=True,
        )
    assert engine.semantic_digest() == before
