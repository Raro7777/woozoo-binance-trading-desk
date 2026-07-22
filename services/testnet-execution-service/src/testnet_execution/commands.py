"""Closed deterministic builders for same-identity query and safety-cancel commands."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, cast

from .canonical import CanonicalValue, canonical_digest


ExistingCommandType = Literal["QUERY_EXISTING_ORDER", "CANCEL_EXISTING_ORDER"]


def build_existing_order_command(
    original: dict[str, object],
    *,
    command_type: ExistingCommandType,
    sequence: int,
    issued_at: datetime,
    query_reason: Literal["UNKNOWN_OUTCOME", "PERIODIC_RECONCILIATION", "OPERATOR_CANCEL"],
) -> dict[str, object]:
    """Preserve the create-order identity; this function cannot create a replacement."""

    if original.get("command_type") != "SUBMIT_LIMIT_ORDER" or sequence < 1:
        raise ValueError("ORIGINAL_SUBMIT_COMMAND_REQUIRED")
    expected_reason = (
        {"UNKNOWN_OUTCOME", "PERIODIC_RECONCILIATION"}
        if command_type == "QUERY_EXISTING_ORDER"
        else {"OPERATOR_CANCEL"}
    )
    if query_reason not in expected_reason:
        raise ValueError("EXISTING_ORDER_REASON_INVALID")
    effect_class = "OBSERVE_ONLY" if command_type == "QUERY_EXISTING_ORDER" else "REDUCE_OR_CANCEL"
    capability = (
        "SPOT_TESTNET_QUERY_BY_CLIENT_ID"
        if command_type == "QUERY_EXISTING_ORDER"
        else "SPOT_TESTNET_CANCEL_BY_CLIENT_ID"
    )
    original_command_id = str(original["command_id"])
    command_id = canonical_digest(
        ["woozoo.testnet-existing-order-command/v1", original_command_id, command_type, sequence]
    )
    request_digest = canonical_digest(
        cast(
            CanonicalValue,
            [
                "woozoo.testnet-existing-order-request/v1",
                command_id,
                original_command_id,
                original["client_order_id"],
                query_reason,
            ],
        )
    )
    expires_at = issued_at + timedelta(seconds=30)
    return {
        "schema_version": "woozoo.testnet-gateway-command/v1",
        "command_id": command_id,
        "command_type": command_type,
        "effect_class": effect_class,
        "producer": "testnet-execution-service",
        "environment": original["environment"],
        "account_binding_id": original["account_binding_id"],
        "account_generation": original["account_generation"],
        "idempotency_key": f"{command_type.lower()}:{original_command_id}:{sequence}",
        "request_digest": request_digest,
        "correlation_id": original_command_id,
        "causation_id": original_command_id,
        "capability_id": capability,
        "gateway_allowlist_digest": original["gateway_allowlist_digest"],
        "gateway_configuration_digest": original["gateway_configuration_digest"],
        "activation_version": original["activation_version"],
        "testnet_barrier_version": original["testnet_barrier_version"],
        "original_authorization_id": original["original_authorization_id"],
        "original_approval_id": original["original_approval_id"],
        "original_proposal_hash": original["original_proposal_hash"],
        "original_risk_decision_hash": original["original_risk_decision_hash"],
        "client_order_id": original["client_order_id"],
        "symbol": original["symbol"],
        "side": original["side"],
        "quantity": original["quantity"],
        "limit_price": original["limit_price"],
        "query_reason": query_reason,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    }


def command_request_digest(command: dict[str, object]) -> str:
    return str(command["request_digest"])


def command_payload_digest(command: dict[str, object]) -> str:
    """Audit helper; not the signed request digest stored inside the command."""

    return canonical_digest(cast(CanonicalValue, command))
