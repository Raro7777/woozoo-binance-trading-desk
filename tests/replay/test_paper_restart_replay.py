from paper_engine import ExecutionFixture, OrderSide, PaperEngine


def run_recorded_sequence() -> PaperEngine:
    engine = PaperEngine()
    engine.seed_balance("USDT", "200", seed_id="seed")
    auth = ExecutionFixture("auth", "nonce", "a" * 64)
    order = engine.create_limit_order(
        idempotency_key="create",
        client_order_id="client",
        authorization=auth,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.01",
        limit_price_text="10000",
    )
    engine.apply_book_observation(
        order_id=order.order_id,
        observation_id="book-1",
        best_bid_text="9999",
        best_ask_text="10000",
        displayed_quantity_text="0.05",
    )
    engine.apply_book_observation(
        order_id=order.order_id,
        observation_id="book-1",
        best_bid_text="9999",
        best_ask_text="10000",
        displayed_quantity_text="99",
    )
    engine.cancel(order.order_id, cancel_id="cancel")
    return engine


def test_ord_002_restart_replay_has_identical_digest_and_single_effect() -> None:
    first = run_recorded_sequence()
    restarted = run_recorded_sequence()
    assert restarted.semantic_digest() == first.semantic_digest()
    assert len(restarted.orders) == 1
    assert len(restarted.fills) == 1
    assert len(restarted.journals) == 2


def test_atom_002_ack_loss_retry_returns_same_order_without_new_effect() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "200", seed_id="seed")
    auth = ExecutionFixture("auth", "nonce", "a" * 64)
    kwargs = dict(
        idempotency_key="create",
        client_order_id="client",
        authorization=auth,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity_text="0.01",
        limit_price_text="10000",
    )
    first = engine.create_limit_order(**kwargs)
    before = engine.semantic_digest()
    retried = engine.create_limit_order(**kwargs)
    assert retried == first
    assert engine.semantic_digest() == before
