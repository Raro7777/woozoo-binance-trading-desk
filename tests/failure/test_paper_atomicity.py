import pytest

from paper_engine import ExecutionFixture, OrderSide, PaperEngine


def test_atom_001_injected_commit_failure_rolls_back_every_effect() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "200", seed_id="seed")
    before = engine.semantic_digest()
    with pytest.raises(RuntimeError, match="INJECTED_COMMIT_FAILURE"):
        engine.atomic(
            lambda: engine.create_limit_order(
                idempotency_key="create",
                client_order_id="client",
                authorization=ExecutionFixture("auth", "nonce", "a" * 64),
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                quantity_text="0.01",
                limit_price_text="10000",
            ),
            fail_after=True,
        )
    assert engine.semantic_digest() == before
    assert engine.orders == {}
    assert engine.outbox == ()
