from datetime import UTC, datetime
from decimal import Decimal

import pytest

from paper_engine import (
    ExecutionFixture,
    MarkFixture,
    OrderSide,
    OrderStatus,
    PaperEngine,
)
from paper_engine.decimal_policy import decimal_input
from paper_engine.models import Journal, LedgerEntry


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
    assert engine.orders[order.order_id].held_amount == Decimal("80.080000000000000000")
    cancelled = engine.cancel(order.order_id, cancel_id="cancel")
    assert cancelled.status == OrderStatus.CANCELLED
    assert cancelled.held_amount == 0
    assert engine.held["USDT"] == 0
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


def test_shared_observation_uses_one_canonical_broker_sequence_and_budget() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    first = engine.create_limit_order(
        idempotency_key="shared-first",
        client_order_id="shared-first",
        authorization=authorization(1),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.05",
        limit_price_text="100",
    )
    second = engine.create_limit_order(
        idempotency_key="shared-second",
        client_order_id="shared-second",
        authorization=authorization(2),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.1",
        limit_price_text="100",
    )

    first_fill = engine.apply_book_observation(
        order_id=first.order_id,
        observation_id="shared-book",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="2",
    )
    after_first = engine.broker_seq
    second_fill = engine.apply_book_observation(
        order_id=second.order_id,
        observation_id="shared-book",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="2",
    )

    assert first_fill is not None and second_fill is not None
    assert first_fill.broker_seq == second_fill.broker_seq
    assert engine.broker_seq == after_first
    assert engine.observation_sequences["shared-book"] == first_fill.broker_seq
    assert engine.observation_budgets["shared-book"][1] == Decimal("0.05")


@pytest.mark.parametrize(
    ("displayed", "expected_fill_quantity"),
    [
        ("0.000099999999999999", Decimal(0)),
        ("0.000100000000000000", Decimal("0.000010000000000000")),
        ("0.000100000000000001", Decimal("0.000010000000000000")),
    ],
)
def test_participation_budget_floors_exact_product_before_scale_quantization(
    displayed: str, expected_fill_quantity: Decimal
) -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "100", seed_id=f"budget-seed-{displayed}")
    order = engine.create_limit_order(
        idempotency_key=f"budget-create-{displayed}",
        client_order_id=f"budget-client-{displayed}",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.00003",
        limit_price_text="200000",
    )

    fill = engine.apply_book_observation(
        order_id=order.order_id,
        observation_id=f"budget-book-{displayed}",
        best_bid_text="199999",
        best_ask_text="200000",
        displayed_quantity_text=displayed,
    )

    assert engine.observation_budgets[f"budget-book-{displayed}"][1] == Decimal(0)
    assert (fill.quantity if fill is not None else Decimal(0)) == expected_fill_quantity


def test_ineligible_later_order_cannot_consume_observation_before_eligible_first_order() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="mixed-canonical-cash")
    first = engine.create_limit_order(
        idempotency_key="mixed-canonical-first",
        client_order_id="mixed-canonical-first",
        authorization=authorization(1),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.1",
        limit_price_text="100",
    )
    later = engine.create_limit_order(
        idempotency_key="mixed-canonical-later",
        client_order_id="mixed-canonical-later",
        authorization=authorization(2),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.1",
        limit_price_text="90",
    )
    before = engine.semantic_digest()

    with pytest.raises(ValueError, match="NON_CANONICAL_OBSERVATION_ORDER"):
        engine.apply_book_observation(
            order_id=later.order_id,
            observation_id="mixed-canonical-book",
            best_bid_text="94",
            best_ask_text="95",
            displayed_quantity_text="1",
        )

    assert engine.semantic_digest() == before
    assert engine.observation_budgets == {}
    assert engine.observation_effects == set()
    assert (
        engine.apply_book_observation(
            order_id=first.order_id,
            observation_id="mixed-canonical-book",
            best_bid_text="94",
            best_ask_text="95",
            displayed_quantity_text="1",
        )
        is not None
    )


def test_ineligible_first_order_can_record_no_fill_before_eligible_later_order() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="mixed-forward-cash")
    first = engine.create_limit_order(
        idempotency_key="mixed-forward-first",
        client_order_id="mixed-forward-first",
        authorization=authorization(1),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.1",
        limit_price_text="90",
    )
    later = engine.create_limit_order(
        idempotency_key="mixed-forward-later",
        client_order_id="mixed-forward-later",
        authorization=authorization(2),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.1",
        limit_price_text="100",
    )

    assert (
        engine.apply_book_observation(
            order_id=first.order_id,
            observation_id="mixed-forward-book",
            best_bid_text="94",
            best_ask_text="95",
            displayed_quantity_text="1",
        )
        is None
    )
    assert (
        engine.apply_book_observation(
            order_id=later.order_id,
            observation_id="mixed-forward-book",
            best_bid_text="94",
            best_ask_text="95",
            displayed_quantity_text="1",
        )
        is not None
    )


def test_extreme_sell_balance_overflow_rolls_back_every_observation_effect() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "99999999999999999999", seed_id="max-cash")
    engine.seed_balance("BTC", "1", seed_id="sell-inventory")
    order = engine.create_limit_order(
        idempotency_key="extreme-sell",
        client_order_id="extreme-sell",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity_text="1",
        limit_price_text="100",
    )
    before = engine.semantic_digest()

    with pytest.raises(ValueError, match=r"NUMERIC\(38,18\)"):
        engine.apply_book_observation(
            order_id=order.order_id,
            observation_id="extreme-sell-book",
            best_bid_text="100",
            best_ask_text="101",
            displayed_quantity_text="10",
        )

    assert engine.semantic_digest() == before


def test_out_of_range_replacement_ledger_is_rejected_before_reversal_mutation() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "100", seed_id="replacement-cash")
    original_key = next(iter(engine.journals))
    before = engine.semantic_digest()
    overflow = Decimal("100000000000000000000")

    with pytest.raises(ValueError, match=r"NUMERIC\(38,18\)"):
        engine.reverse_and_replace(
            original_key=original_key,
            correction_id="overflow-correction",
            replacement_entries=(
                LedgerEntry("paper.available", "USDT", overflow, Decimal(0)),
                LedgerEntry("paper.opening-equity", "USDT", Decimal(0), overflow),
            ),
        )

    assert engine.semantic_digest() == before


def test_journal_rejects_scale_eighteen_imbalance_hidden_by_default_decimal_context() -> None:
    debit = Decimal("99999999999999999999.000000000000000000")
    credit = Decimal("99999999999999999998.999999999999999999")
    journal = Journal(
        "0" * 64,
        "paper.test",
        "precision-counterexample",
        "PHYSICAL",
        (
            LedgerEntry("paper.available", "USDT", debit, Decimal(0)),
            LedgerEntry("paper.equity", "USDT", Decimal(0), credit),
        ),
    )

    with pytest.raises(ValueError, match="LEDGER_IMBALANCE:USDT"):
        journal.assert_balanced()

    engine = PaperEngine()
    engine.seed_balance("USDT", "100", seed_id="precision-ledger-seed")
    before = engine.semantic_digest()
    with pytest.raises(ValueError, match="LEDGER_IMBALANCE:USDT"):
        engine.reverse_and_replace(
            original_key=next(iter(engine.journals)),
            correction_id="precision-counterexample",
            replacement_entries=journal.entries,
        )
    assert engine.semantic_digest() == before


def test_pre_acceptance_observation_is_ignored_without_recording_an_effect() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    first = engine.create_limit_order(
        idempotency_key="old-book-first",
        client_order_id="old-book-first",
        authorization=authorization(1),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    engine.apply_book_observation(
        order_id=first.order_id,
        observation_id="old-book",
        best_bid_text="99",
        best_ask_text="100",
        displayed_quantity_text="1",
    )
    second = engine.create_limit_order(
        idempotency_key="old-book-second",
        client_order_id="old-book-second",
        authorization=authorization(2),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    third = engine.create_limit_order(
        idempotency_key="old-book-third",
        client_order_id="old-book-third",
        authorization=authorization(3),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    prior_budget = engine.observation_budgets["old-book"]
    prior_seq = engine.broker_seq

    assert (
        engine.apply_book_observation(
            order_id=third.order_id,
            observation_id="old-book",
            best_bid_text="99",
            best_ask_text="100",
            displayed_quantity_text="1",
        )
        is None
    )
    assert (
        engine.apply_book_observation(
            order_id=second.order_id,
            observation_id="old-book",
            best_bid_text="99",
            best_ask_text="100",
            displayed_quantity_text="1",
        )
        is None
    )
    assert (second.order_id, "old-book") not in engine.observation_effects
    assert (third.order_id, "old-book") not in engine.observation_effects
    assert engine.observation_budgets["old-book"] == prior_budget
    assert engine.broker_seq == prior_seq


def test_ledger_transaction_id_rejects_unicode_and_non_hash_ids() -> None:
    journal = Journal(
        "원장-1",
        "paper.seed",
        "seed",
        "PHYSICAL",
        (
            LedgerEntry("paper.available", "USDT", Decimal(1), Decimal(0)),
            LedgerEntry("paper.opening-equity", "USDT", Decimal(0), Decimal(1)),
        ),
    )
    with pytest.raises(ValueError, match="INVALID_LEDGER_TRANSACTION_ID"):
        journal.assert_balanced()


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


@pytest.mark.parametrize(
    ("field", "value"),
    (("authorization_id", "승인-1"), ("authorization_nonce", "nonce-한글")),
)
def test_canonical_event_identity_rejects_non_ascii_fixture_ids(field: str, value: str) -> None:
    fixture = authorization()
    values = {
        "authorization_id": fixture.authorization_id,
        "authorization_nonce": fixture.authorization_nonce,
        "preview_hash": fixture.preview_hash,
    }
    values[field] = value
    with pytest.raises(ValueError, match="INVALID_OPAQUE_ID"):
        ExecutionFixture(**values).validate()


def test_canonical_event_identity_rejects_non_ascii_cancel_id() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "1000", seed_id="cash")
    order = engine.create_limit_order(
        idempotency_key="create-ascii",
        client_order_id="client-ascii",
        authorization=authorization(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="1",
        limit_price_text="100",
    )
    with pytest.raises(ValueError, match="INVALID_OPAQUE_ID"):
        engine.cancel(order.order_id, cancel_id="취소-1")


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
