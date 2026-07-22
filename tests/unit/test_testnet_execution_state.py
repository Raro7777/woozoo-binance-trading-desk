from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from testnet_execution.canonical import canonical_digest, derive_client_order_id
from testnet_execution.models import (
    AccountGeneration,
    CommandOutcome,
    OrderObservation,
    OrderState,
    TestnetAuthorization,
)
from testnet_execution.state_machine import ExecutionState


HASH = "a" * 64
NOW = datetime(2026, 7, 22, tzinfo=UTC)


def authorization(**overrides: object) -> TestnetAuthorization:
    values: dict[str, object] = {
        "authorization_id": "b" * 64,
        "approval_id": "c" * 64,
        "proposal_hash": "d" * 64,
        "risk_decision_hash": "e" * 64,
        "preview_digest": "f" * 64,
        "account_binding_id": HASH,
        "account_generation": 1,
        "client_order_id": "wz8-" + "1" * 32,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "revoked": False,
    }
    values.update(overrides)
    return TestnetAuthorization(**values)  # type: ignore[arg-type]


def test_canonical_digest_and_client_order_id_are_stable_and_generation_bound() -> None:
    digest = canonical_digest({"quantity": "0.001", "price": "60000.00"})
    assert len(digest) == 64
    first = derive_client_order_id("b" * 64, "nonce-1", 1)
    assert first == derive_client_order_id("b" * 64, "nonce-1", 1)
    assert first != derive_client_order_id("b" * 64, "nonce-1", 2)
    assert first.startswith("wz8-") and len(first) == 36 and first.isascii()


def test_authorization_drift_or_unknown_outcome_never_mints_replacement() -> None:
    state = ExecutionState(AccountGeneration(1, "ACTIVE", HASH))
    auth = authorization()
    receipt = state.issue_submit(auth, execution_nonce="nonce-1", now=NOW)
    replay = state.issue_submit(auth, execution_nonce="nonce-1", now=NOW)
    assert replay == receipt
    assert state.create_effect_count == 1

    state.record_dispatch(receipt.command_id)
    state.record_outcome(receipt.command_id, CommandOutcome.SUBMISSION_UNKNOWN)
    query = state.query_unknown(receipt.command_id)
    assert query.client_order_id == receipt.client_order_id
    assert query.command_type == "QUERY_EXISTING_ORDER"
    assert state.create_effect_count == 1
    with pytest.raises(ValueError, match="AUTHORIZATION_ALREADY_CONSUMED"):
        state.issue_submit(auth, execution_nonce="nonce-2", now=NOW)


def test_unknown_not_found_requires_two_distinct_authoritative_observations() -> None:
    state = ExecutionState(AccountGeneration(1, "ACTIVE", HASH))
    receipt = state.issue_submit(authorization(), execution_nonce="nonce-1", now=NOW)
    state.record_dispatch(receipt.command_id)
    state.record_outcome(receipt.command_id, CommandOutcome.SUBMISSION_UNKNOWN)

    assert (
        state.record_unknown_query_result(
            receipt.command_id,
            observation_id="missing-1",
            authoritative=True,
            found=False,
        )
        is CommandOutcome.SUBMISSION_UNKNOWN
    )
    assert (
        state.record_unknown_query_result(
            receipt.command_id,
            observation_id="missing-1",
            authoritative=True,
            found=False,
        )
        is CommandOutcome.SUBMISSION_UNKNOWN
    )
    with pytest.raises(ValueError, match="OBSERVATION_NOT_AUTHORITATIVE"):
        state.record_unknown_query_result(
            receipt.command_id,
            observation_id="cache-miss",
            authoritative=False,
            found=False,
        )
    assert (
        state.record_unknown_query_result(
            receipt.command_id,
            observation_id="missing-2",
            authoritative=True,
            found=False,
        )
        is CommandOutcome.NOT_FOUND_CONFIRMED
    )
    assert state.create_effect_count == 1


def test_duplicate_and_out_of_order_observations_are_monotonic() -> None:
    state = ExecutionState(AccountGeneration(1, "ACTIVE", HASH))
    receipt = state.issue_submit(authorization(), execution_nonce="nonce-1", now=NOW)
    observations = (
        OrderObservation("obs-2", receipt.client_order_id, 1, "PARTIALLY_FILLED", "0.4", 2),
        OrderObservation("obs-1", receipt.client_order_id, 1, "NEW", "0", 1),
        OrderObservation("obs-3", receipt.client_order_id, 1, "FILLED", "1.0", 3),
        OrderObservation("obs-2", receipt.client_order_id, 1, "PARTIALLY_FILLED", "0.4", 2),
    )
    for item in observations:
        state.apply_order_observation(item, original_quantity="1.0")
    order = state.orders[receipt.client_order_id]
    assert order.status is OrderState.FILLED
    assert str(order.filled_quantity) == "1.0"
    assert state.observation_effect_count == 3

    with pytest.raises(ValueError, match="OVERFILL"):
        state.apply_order_observation(
            OrderObservation("obs-4", receipt.client_order_id, 1, "FILLED", "1.1", 4),
            original_quantity="1.0",
        )
    assert state.testnet_barrier_active is True


def test_reset_confirmation_does_not_restore_until_new_generation_is_healthy() -> None:
    state = ExecutionState(AccountGeneration(1, "ACTIVE", HASH))
    state.suspect_reset("checkpoint-1")
    assert state.new_commands_allowed is False
    state.confirm_reset("checkpoint-1", actor_id="operator-local-1")
    assert state.generation.number == 2
    assert state.generation.status == "PENDING_RECONCILIATION"
    assert state.new_commands_allowed is False
    with pytest.raises(ValueError, match="OLD_ACCOUNT_GENERATION"):
        state.apply_order_observation(
            OrderObservation("late", "wz8-" + "a" * 32, 1, "NEW", "0", 1),
            original_quantity="1.0",
        )
    state.record_healthy_checkpoint(HASH, account_generation=2)
    assert state.new_commands_allowed is True
