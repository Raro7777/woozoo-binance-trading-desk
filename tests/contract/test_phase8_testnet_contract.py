from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "packages" / "contracts" / "spec"

PHASE8_SCHEMAS = {
    "testnet-order-preview.v1.json": "woozoo.testnet-order-preview/v1",
    "testnet-risk-input.v1.json": "woozoo.testnet-risk-input/v1",
    "testnet-risk-decision.v1.json": "woozoo.testnet-risk-decision/v1",
    "testnet-approval.v1.json": "woozoo.testnet-approval/v1",
    "testnet-approval-revocation.v1.json": "woozoo.testnet-approval-revocation/v1",
    "testnet-execution-authorization.v1.json": ("woozoo.testnet-execution-authorization/v1"),
    "testnet-gateway-command.v1.json": "woozoo.testnet-gateway-command/v1",
    "testnet-gateway-receipt.v1.json": "woozoo.testnet-gateway-receipt/v1",
    "testnet-order.v1.json": "woozoo.testnet-order/v1",
    "testnet-reconciliation.v1.json": "woozoo.testnet-reconciliation/v1",
    "testnet-account-generation.v1.json": "woozoo.testnet-account-generation/v1",
    "testnet-gateway-status.v1.json": "woozoo.testnet-gateway-status/v1",
    "testnet-domain-events.v1.json": "woozoo.testnet-domain-events/v1",
}


def load(name: str) -> dict[str, object]:
    return json.loads((SPEC / name).read_text("utf-8"))


def test_phase8_contracts_are_closed_separate_and_active_only_in_phase8() -> None:
    for name, schema_id in PHASE8_SCHEMAS.items():
        schema = load(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == schema_id
        assert schema["x-creation-phase"] == schema["x-activation-phase"] == 8
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False

    assert load("testnet-risk-input.v1.json")["properties"]["namespace"] == {"const": "testnet"}
    assert load("testnet-execution-authorization.v1.json")["properties"]["environment"] == {
        "const": "BINANCE_SPOT_TESTNET"
    }
    assert load("paper-execution-authorization.v1.json")["properties"]["namespace"] == {
        "const": "paper"
    }


def test_testnet_approval_binds_full_authority_and_rejects_unknown_fields() -> None:
    schema = load("testnet-approval.v1.json")
    required = set(schema["required"])
    assert {
        "approval_id",
        "proposal_id",
        "proposal_hash",
        "risk_decision_id",
        "risk_decision_hash",
        "risk_input_digest",
        "risk_policy_version",
        "preview_policy_version",
        "environment",
        "account_binding_id",
        "account_generation",
        "testnet_order_preview",
        "testnet_order_preview_digest",
        "approval_input_digest",
        "actor_id",
        "session_binding_hash",
        "csrf_binding_hash",
        "origin_hash",
        "decision",
        "approval_nonce",
        "approved_at",
        "expires_at",
        "revocation_state",
        "payload_hash",
    } <= required
    assert schema["properties"]["actor_id"] == {"const": "operator-local-1"}
    assert schema["properties"]["environment"] == {"const": "BINANCE_SPOT_TESTNET"}
    assert "CONSUMED" not in schema["properties"]["validity"]["enum"]

    validator = Draft202012Validator(schema)
    invalid = {field: None for field in required}
    invalid["unexpected"] = "unsafe"
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_gateway_command_closes_effect_capabilities_and_unknown_never_resubmits() -> None:
    command = load("testnet-gateway-command.v1.json")
    assert set(command["properties"]["command_type"]["enum"]) == {
        "SUBMIT_LIMIT_ORDER",
        "CANCEL_EXISTING_ORDER",
        "QUERY_EXISTING_ORDER",
        "RECONCILIATION_OBSERVATION",
    }
    assert set(command["properties"]["effect_class"]["enum"]) == {
        "CREATE_ORDER",
        "REDUCE_OR_CANCEL",
        "OBSERVE_ONLY",
    }
    assert command["properties"]["producer"] == {"const": "testnet-execution-service"}
    assert command["properties"]["environment"] == {"const": "BINANCE_SPOT_TESTNET"}
    assert "original_authorization_id" in command["required"]
    assert "original_approval_id" in command["required"]
    assert "original_proposal_hash" in command["required"]
    assert "original_risk_decision_hash" in command["required"]

    receipt = load("testnet-gateway-receipt.v1.json")
    statuses = set(receipt["properties"]["status"]["enum"])
    assert "SUBMISSION_UNKNOWN" in statuses
    assert "RESOLVED_FOUND" in statuses
    assert "RESOLVED_REJECTED" in statuses
    assert "RESOLVED_NOT_FOUND_CONFIRMED" in statuses
    assert not any("RETRY" in status or "REPLAC" in status for status in statuses)


def test_reconciliation_and_generation_are_reset_aware_and_fail_closed() -> None:
    reconciliation = load("testnet-reconciliation.v1.json")
    statuses = set(reconciliation["properties"]["status"]["enum"])
    assert statuses == {
        "HEALTHY",
        "RUNNING",
        "FAILED",
        "RESET_SUSPECTED",
        "AWAITING_OPERATOR_CONFIRMATION",
        "UNKNOWN",
    }
    assert {
        "account_generation",
        "checkpoint_id",
        "checkpoint_digest",
        "observation_set_digest",
        "confirmed_by_operator",
        "new_commands_allowed",
    } <= set(reconciliation["required"])

    generation = load("testnet-account-generation.v1.json")
    assert generation["properties"]["environment"] == {"const": "BINANCE_SPOT_TESTNET"}
    assert set(generation["properties"]["status"]["enum"]) == {
        "PENDING_RECONCILIATION",
        "ACTIVE",
        "RESET_SUSPECTED",
        "AWAITING_OPERATOR_CONFIRMATION",
        "RETIRED",
    }


def test_phase8_openapi_is_browser_only_and_has_no_exchange_effect_route() -> None:
    paths = load("openapi.v1.json")["paths"]
    required = {
        "/api/v1/testnet/operator-state",
        "/api/v1/testnet-activations",
        "/api/v1/testnet-activations/{activation_id}/deactivations",
        "/api/v1/proposals/{proposal_id}/testnet-approval-view",
        "/api/v1/testnet-approvals",
        "/api/v1/testnet-approvals/{approval_id}/revocations",
        "/api/v1/testnet-executions/{execution_id}",
        "/api/v1/testnet-reconciliation/{checkpoint_id}/confirmations",
    }
    assert required <= set(paths)
    forbidden_fragments = (
        "/gateway",
        "/exchange",
        "/balances",
        "/account",
        "/submit",
        "/replace",
        "/retry",
        "/query-order",
        "/internal/",
    )
    assert not any(fragment in path for path in paths for fragment in forbidden_fragments)

    mutations = {
        path: item["post"] for path, item in paths.items() if path in required and "post" in item
    }
    for path, operation in mutations.items():
        assert operation["security"] == [{"LocalOperatorSession": []}], path
        refs = {parameter["$ref"] for parameter in operation["parameters"] if "$ref" in parameter}
        assert {
            "#/components/parameters/OriginHeader",
            "#/components/parameters/CsrfHeader",
            "#/components/parameters/IdempotencyHeader",
            "#/components/parameters/IfMatchHeader",
        } <= refs

    approval_command = load("openapi.v1.json")["components"]["schemas"]["TestnetApprovalCommandV1"]
    assert set(approval_command["properties"]) == {
        "proposal_id",
        "decision",
        "expected_version",
        "testnet_order_preview_digest",
        "approval_input_digest",
        "reason",
    }


def test_phase8_contract_manifest_contains_every_phase8_schema() -> None:
    manifest = json.loads(
        (ROOT / "packages" / "contracts" / "schema-manifest.json").read_text("utf-8")
    )
    assert manifest["testnet_activation_phase"] == 8
    assert set(PHASE8_SCHEMAS) <= set(manifest["sources"])
