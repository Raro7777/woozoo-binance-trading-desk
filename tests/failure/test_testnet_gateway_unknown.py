from __future__ import annotations

from dataclasses import replace

import pytest

from spot_testnet_gateway.dispatch import (
    ActivationAuthority,
    GatewayCommand,
    GatewayDispatcher,
    InMemoryDispatchStore,
    GatewayRuntimeBinding,
    TransportResult,
)


HASH = "a" * 64
CLIENT_ID = "wz8-" + "b" * 32


def activation(**overrides: object) -> ActivationAuthority:
    values: dict[str, object] = {
        "deployment_enabled": True,
        "operator_active": True,
        "environment": "BINANCE_SPOT_TESTNET",
        "account_generation": 1,
        "gateway_instance_id": HASH,
        "build_digest": HASH,
        "configuration_digest": HASH,
        "allowlist_digest": HASH,
        "gateway_health": "READY",
        "paper_kill_active": False,
        "testnet_barrier_active": False,
        "reconciliation_status": "HEALTHY",
    }
    values.update(overrides)
    return ActivationAuthority(**values)  # type: ignore[arg-type]


def command(**overrides: object) -> GatewayCommand:
    values: dict[str, object] = {
        "command_id": "c" * 64,
        "request_digest": "d" * 64,
        "command_type": "SUBMIT_LIMIT_ORDER",
        "effect_class": "CREATE_ORDER",
        "environment": "BINANCE_SPOT_TESTNET",
        "account_generation": 1,
        "client_order_id": CLIENT_ID,
        "configuration_digest": HASH,
        "allowlist_digest": HASH,
    }
    values.update(overrides)
    return GatewayCommand(**values)  # type: ignore[arg-type]


def runtime(**overrides: object) -> GatewayRuntimeBinding:
    values: dict[str, object] = {
        "gateway_instance_id": HASH,
        "build_digest": HASH,
        "configuration_digest": HASH,
        "allowlist_digest": HASH,
    }
    values.update(overrides)
    return GatewayRuntimeBinding(**values)  # type: ignore[arg-type]


def test_response_loss_is_unknown_and_replay_never_sends_twice() -> None:
    store = InMemoryDispatchStore()
    dispatcher = GatewayDispatcher(store, runtime())
    calls: list[str] = []

    def lost_response(item: GatewayCommand) -> TransportResult:
        calls.append(item.client_order_id)
        raise TimeoutError("response intentionally lost")

    first = dispatcher.dispatch(command(), activation(), lost_response)
    replay = dispatcher.dispatch(command(), activation(), lost_response)
    assert first.status == replay.status == "SUBMISSION_UNKNOWN"
    assert first.client_order_id == replay.client_order_id == CLIENT_ID
    assert calls == [CLIENT_ID]
    assert store.dispatch_attempt_count == 1
    assert store.external_send_count == 1


@pytest.mark.parametrize(
    "authority",
    [
        activation(deployment_enabled=False),
        activation(operator_active=False),
        activation(environment="UNKNOWN"),
        activation(gateway_health="DEGRADED"),
        activation(paper_kill_active=True),
        activation(testnet_barrier_active=True),
        activation(reconciliation_status="FAILED"),
    ],
)
def test_fail_closed_authority_creates_zero_dispatch_and_network_effect(
    authority: ActivationAuthority,
) -> None:
    store = InMemoryDispatchStore()
    calls = 0

    def should_not_send(_: GatewayCommand) -> TransportResult:
        nonlocal calls
        calls += 1
        return TransportResult("ACKNOWLEDGED")

    receipt = GatewayDispatcher(store, runtime()).dispatch(command(), authority, should_not_send)
    assert receipt.status == "REJECTED_LOCAL"
    assert calls == 0
    assert store.dispatch_attempt_count == 0
    assert store.external_send_count == 0


def test_same_command_id_with_different_digest_is_conflict() -> None:
    store = InMemoryDispatchStore()
    dispatcher = GatewayDispatcher(store, runtime())
    dispatcher.dispatch(command(), activation(), lambda _: TransportResult("ACKNOWLEDGED"))
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        dispatcher.dispatch(
            replace(command(), request_digest="e" * 64),
            activation(),
            lambda _: TransportResult("ACKNOWLEDGED"),
        )


def test_unknown_query_is_allowed_during_barrier_but_never_create() -> None:
    store = InMemoryDispatchStore()
    authority = activation(testnet_barrier_active=True, reconciliation_status="FAILED")
    query = command(
        command_type="QUERY_EXISTING_ORDER",
        effect_class="OBSERVE_ONLY",
    )
    receipt = GatewayDispatcher(store, runtime()).dispatch(
        query, authority, lambda _: TransportResult("ACKNOWLEDGED")
    )
    assert receipt.status == "EXCHANGE_ACKNOWLEDGED"
    assert store.external_send_count == 1

    create_receipt = GatewayDispatcher(InMemoryDispatchStore(), runtime()).dispatch(
        command(), authority, lambda _: TransportResult("ACKNOWLEDGED")
    )
    assert create_receipt.status == "REJECTED_LOCAL"


@pytest.mark.parametrize(
    "binding",
    [
        runtime(gateway_instance_id="f" * 64),
        runtime(build_digest="f" * 64),
        runtime(configuration_digest="f" * 64),
        runtime(allowlist_digest="f" * 64),
    ],
)
def test_runtime_identity_drift_creates_zero_effect(binding: GatewayRuntimeBinding) -> None:
    store = InMemoryDispatchStore()
    calls = 0

    def should_not_send(_: GatewayCommand) -> TransportResult:
        nonlocal calls
        calls += 1
        return TransportResult("ACKNOWLEDGED")

    receipt = GatewayDispatcher(store, binding).dispatch(command(), activation(), should_not_send)
    assert receipt.status == "REJECTED_LOCAL"
    assert calls == 0
