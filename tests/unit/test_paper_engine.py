from datetime import UTC, datetime
from decimal import Decimal

import pytest

from paper_engine import ExecutionFixture, MarkFixture, OrderSide, OrderStatus, PaperEngine
from paper_engine.decimal_policy import decimal_input


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


def test_fin_001_hold_fill_fee_valuation_and_release_are_all_journaled() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "200", seed_id="seed")
    order = engine.create_limit_order(
        idempotency_key="create",
        client_order_id="client",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.01",
        limit_price_text="10000",
    )
    assert [journal.business_event_type for journal in engine.journals.values()].count(
        "paper.hold"
    ) == 1
    engine.apply_book_observation(
        order_id=order.order_id,
        observation_id="book",
        best_bid_text="9999",
        best_ask_text="10000",
        displayed_quantity_text="0.05",
    )
    engine.cancel(order.order_id, cancel_id="cancel")
    event_kinds = [
        (journal.business_event_type, journal.journal_kind) for journal in engine.journals.values()
    ]
    assert ("paper.fill", "PHYSICAL") in event_kinds
    assert ("paper.fill", "VALUATION") in event_kinds
    assert ("paper.hold-release", "PHYSICAL") in event_kinds
    physical = [
        entry.account
        for journal in engine.journals.values()
        if journal.business_event_type == "paper.fill" and journal.journal_kind == "PHYSICAL"
        for entry in journal.entries
    ]
    assert "paper.fee" in physical


def test_decimal_rejects_more_than_twenty_integer_digits() -> None:
    with pytest.raises(ValueError, match="NUMERIC"):
        decimal_input("100000000000000000000")


def test_order_side_must_be_closed_enum() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="seed")
    with pytest.raises(ValueError, match="INVALID_ORDER_SIDE"):
        engine.create_limit_order(
            idempotency_key="bad",
            client_order_id="bad",
            authorization=authorization(),
            symbol="BTCUSDT",
            side="NOT_A_SIDE",
            quantity_text="1",
            limit_price_text="100",
        )


def test_actual_outbox_event_matches_closed_envelope() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="seed")
    order = engine.create_limit_order(
        idempotency_key="create",
        client_order_id="client",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    assert set(engine.outbox[0]) == {
        "spec_version",
        "event_id",
        "event_type",
        "event_version",
        "occurred_at",
        "producer",
        "activation_phase",
        "aggregate_id",
        "aggregate_version",
        "payload_hash",
        "data",
    }
    assert engine.outbox[0]["spec_version"] == "woozoo.event/v1"
    assert set(engine.order_contract(order.order_id)) == {
        "order_id",
        "client_order_id",
        "authorization_id",
        "authorization_namespace",
        "symbol",
        "side",
        "order_type",
        "time_in_force",
        "quantity",
        "limit_price",
        "filled_quantity",
        "status",
        "version",
    }


def test_fifo_final_consumption_absorbs_decimal_residual() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "100", seed_id="cash")
    buy = engine.create_limit_order(
        idempotency_key="buy",
        client_order_id="buy",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.00015",
        limit_price_text="166666.67",
    )
    engine.apply_book_observation(
        order_id=buy.order_id,
        observation_id="buy-book",
        best_bid_text="99999",
        best_ask_text="166666.67",
        displayed_quantity_text="0.0015",
    )
    for number in range(3):
        sell = engine.create_limit_order(
            idempotency_key=f"sell-{number}",
            client_order_id=f"sell-{number}",
            authorization=authorization(number + 2),
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            quantity_text="0.00005",
            limit_price_text="166666.67",
        )
        engine.apply_book_observation(
            order_id=sell.order_id,
            observation_id=f"sell-book-{number}",
            best_bid_text="166666.67",
            best_ask_text="166666.68",
            displayed_quantity_text="0.0005",
        )
    lot = next(lot for lot in engine.lots if lot.source_fill_id != "cash")
    assert (
        sum(item.quote_basis for item in engine.consumptions if item.lot_id == lot.lot_id)
        == lot.quote_cost
    )


@pytest.mark.parametrize(
    "mark",
    [
        MarkFixture("1", "", datetime(2026, 7, 19, tzinfo=UTC), "VALID", "mark-v1"),
        MarkFixture("1", "fixture", datetime(2026, 7, 19, tzinfo=UTC), "STALE", "mark-v1"),
        MarkFixture("1", "fixture", datetime(2026, 7, 19, tzinfo=UTC), "VALID", ""),
        MarkFixture("1", "fixture", datetime(2026, 7, 19), "VALID", "mark-v1"),
    ],
)
def test_unrealized_pnl_rejects_incomplete_mark_provenance(mark: MarkFixture) -> None:
    with pytest.raises(ValueError, match="INVALID_MARK_PROVENANCE"):
        PaperEngine().unrealized_pnl("BTC", mark)
