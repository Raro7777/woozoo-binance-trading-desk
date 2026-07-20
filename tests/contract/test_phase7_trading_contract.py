import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
SPEC = ROOT / "packages" / "contracts" / "spec"


def load(name: str) -> dict[str, object]:
    return json.loads((SPEC / name).read_text("utf-8"))


def test_phase7_contracts_activate_paper_without_mutating_frozen_test_contracts() -> None:
    frozen_risk = load("risk-input.v2.json")
    frozen_order = load("paper-order.v1.json")
    risk = load("risk-input.v3.json")
    order = load("paper-order.v2.json")

    assert frozen_risk["properties"]["namespace"] == {"const": "test"}
    assert frozen_order["properties"]["authorization_namespace"] == {"const": "test"}
    assert risk["$id"] == "woozoo.risk-input/v3"
    assert risk["additionalProperties"] is False
    assert risk["x-creation-phase"] == risk["x-activation-phase"] == 7
    assert risk["properties"]["namespace"] == {"const": "paper"}
    assert risk["properties"]["preview_policy_version"] == {
        "const": "woozoo.paper-order-preview-policy/v1"
    }
    assert "preview_policy_version" in risk["required"]
    assert "market_books" in risk["required"]
    market_books = risk["properties"]["market_books"]
    assert market_books["additionalProperties"] is False
    assert set(market_books["required"]) == {"BTCUSDT", "ETHUSDT"}
    assert set(market_books["properties"]) == {"BTCUSDT", "ETHUSDT"}
    market_book = risk["$defs"]["market_book"]
    assert market_book["additionalProperties"] is False
    assert set(market_book["required"]) == {"best_bid", "best_ask"}
    assert order["$id"] == "woozoo.paper-order/v2"
    assert order["additionalProperties"] is False
    assert order["properties"]["authorization_namespace"] == {"const": "paper"}
    assert {
        "approval_id",
        "proposal_hash",
        "risk_decision_hash",
        "paper_order_preview_hash",
        "authorization_nonce",
    }.issubset(order["required"])


def test_phase7_approval_chain_is_closed_hash_bound_and_nonce_distinct() -> None:
    approval = load("paper-approval.v1.json")
    revocation = load("paper-approval-revocation.v1.json")
    authorization = load("paper-execution-authorization.v1.json")
    view = load("approval-view.v1.json")

    for schema in (approval, revocation, authorization, view):
        assert schema["additionalProperties"] is False
        assert schema["x-creation-phase"] == schema["x-activation-phase"] == 7

    assert approval["properties"]["actor_id"] == {"const": "operator-local-1"}
    assert set(approval["properties"]["decision"]["enum"]) == {"APPROVED", "REJECTED"}
    assert {
        "proposal_hash",
        "risk_decision_hash",
        "risk_input_digest",
        "risk_policy_version",
        "paper_order_preview",
        "paper_order_preview_hash",
        "approval_nonce",
        "session_binding_hash",
        "csrf_binding_hash",
        "origin_hash",
        "decided_at",
        "expires_at",
    }.issubset(approval["required"])
    assert revocation["properties"]["actor_id"] == {"const": "operator-local-1"}
    assert {"session_binding_hash", "csrf_binding_hash", "origin_hash"}.issubset(
        revocation["required"]
    )
    assert authorization["properties"]["namespace"] == {"const": "paper"}
    assert {
        "approval_nonce_hash",
        "authorization_nonce",
        "authorization_input_digest",
        "current_data_state_hash",
        "reconciliation_checkpoint_hash",
        "ledger_snapshot_hash",
        "issued_at",
        "expires_at",
    }.issubset(authorization["required"])
    assert "approval_nonce" not in authorization["required"]
    assert set(view["properties"]["status"]["enum"]) == {
        "PENDING_RISK",
        "PENDING_APPROVAL",
        "READY",
        "APPROVED",
        "AUTHORIZATION_ISSUED",
        "BLOCKED",
        "INVALID",
    }
    preview = view["$defs"]["preview"]
    assert preview["additionalProperties"] is False
    assert set(preview["properties"]) == set(preview["required"])
    assert {"best_bid", "best_ask", "expected_slippage_inputs"} <= set(preview["required"])
    slippage = preview["properties"]["expected_slippage_inputs"]
    assert slippage["additionalProperties"] is False
    assert set(slippage["properties"]) == set(slippage["required"]) == {"method"}


def test_phase7_session_and_event_contracts_close_secret_and_recovery_boundaries() -> None:
    session = load("local-session.v1.json")
    risk_events = load("risk-domain-events.v2.json")
    paper_events = load("paper-domain-events.v2.json")
    manifest = json.loads(
        (ROOT / "packages" / "contracts" / "schema-manifest.json").read_text("utf-8")
    )

    assert session["additionalProperties"] is False
    assert "session_digest" not in session["properties"]
    assert "csrf_token_digest" not in session["properties"]
    assert session["properties"]["actor_id"] == {"const": "operator-local-1"}
    assert risk_events["$id"] == "woozoo.risk-domain-events/v2"
    assert len(risk_events["oneOf"]) == 5
    recovery = risk_events["$defs"]["killRecoveryData"]
    assert recovery["properties"]["actor_id"] == {"const": "operator-local-1"}
    assert recovery["properties"]["active"] == {"const": False}
    assert {
        "incident_reference",
        "data_state_hash",
        "reconciliation_checkpoint_hash",
        "ledger_snapshot_hash",
        "session_binding_hash",
        "csrf_binding_hash",
        "origin_hash",
        "data_status",
        "reconciliation_status",
        "ledger_status",
    }.issubset(recovery["required"])
    assert recovery["properties"]["data_status"] == {"const": "HEALTHY"}
    assert recovery["properties"]["reconciliation_status"] == {"const": "PASS"}
    assert recovery["properties"]["ledger_status"] == {"const": "BALANCED"}
    assert paper_events["$id"] == "woozoo.paper-domain-events/v2"
    reasons = set(paper_events["$defs"]["blockedData"]["properties"]["reason_code"]["enum"])
    assert {"KILL_ACTIVE", "DATA_STALE", "AUTHORIZATION_EXPIRED", "LEDGER_UNHEALTHY"} <= reasons
    for name in (
        "analysis-run.v2.json",
        "analysis-run-view.v1.json",
        "risk-input.v3.json",
        "paper-order.v2.json",
        "paper-approval.v1.json",
        "paper-approval-revocation.v1.json",
        "paper-execution-authorization.v1.json",
        "approval-view.v1.json",
        "local-session.v1.json",
        "risk-domain-events.v2.json",
        "paper-domain-events.v2.json",
    ):
        assert name in manifest["sources"]


def test_phase7_analysis_contract_preserves_v1_and_activates_mock_paper_v2() -> None:
    frozen = load("analysis-run.v1.json")
    production = load("analysis-run.v2.json")

    assert frozen["properties"]["namespace"] == {"const": "test"}
    assert production["$id"] == "woozoo.analysis-run/v2"
    assert production["additionalProperties"] is False
    assert production["properties"]["namespace"] == {"const": "paper"}
    assert production["properties"]["provider"] == {"const": "mock"}
    assert production["properties"]["tool_count"] == {"const": 0}
    assert "risk_decision_id" in production["required"]


def test_phase7_openapi_is_browser_only_and_closes_mutation_guards() -> None:
    document = load("openapi.v1.json")
    paths = document["paths"]
    assert not any("/internal/" in path for path in paths)
    assert document["components"]["securitySchemes"]["LocalOperatorSession"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-woozoo_session",
    }

    guarded = {
        "/api/v1/session/logout": False,
        "/api/v1/analysis-runs": False,
        "/api/v1/paper-approvals": True,
        "/api/v1/paper-approvals/{approval_id}/revocations": True,
        "/api/v1/paper-orders/{order_id}/cancel": True,
        "/api/v1/kill-switch/activate": True,
        "/api/v1/kill-switch/recover": True,
    }
    common = {
        "#/components/parameters/OriginHeader",
        "#/components/parameters/CsrfHeader",
        "#/components/parameters/IdempotencyHeader",
    }
    for path, versioned in guarded.items():
        operation = paths[path]["post"]
        assert operation["security"] == [{"LocalOperatorSession": []}]
        refs = {parameter["$ref"] for parameter in operation["parameters"]}
        assert common <= refs
        assert ("#/components/parameters/IfMatchHeader" in refs) is versioned

    login = paths["/api/v1/session/login"]["post"]
    assert login["security"] == []
    assert {item["$ref"] for item in login["parameters"]} == {
        "#/components/parameters/OriginHeader"
    }
    assert (
        document["components"]["schemas"]["LoginCommandV1"]["properties"]["password"]["writeOnly"]
        is True
    )
    assert (
        "authorization_nonce"
        not in document["components"]["schemas"]["PaperOrderViewV1"]["properties"]
    )
    analysis_responses = paths["/api/v1/analysis-runs"]["post"]["responses"]
    assert analysis_responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "analysis-run-view.v1.json"
    }
    assert analysis_responses["201"]["content"]["application/json"]["schema"] == {
        "$ref": "analysis-run-view.v1.json"
    }
    approval_receipt = document["components"]["schemas"]["ApprovalCommandReceiptV1"]
    assert "AUTHORIZATION_ISSUED" in approval_receipt["properties"]["result"]["enum"]
    assert "ISSUED" in approval_receipt["properties"]["authorization_status"]["enum"]
    assert "[0-9]+" in document["components"]["parameters"]["IfMatchHeader"]["schema"]["pattern"]

    audit = document["components"]["schemas"]["AuditEventV1"]
    assert {"producer", "actor_id"} <= set(audit["required"])
    assert audit["properties"]["actor_id"]["type"] == ["string", "null"]
    audit_data = document["components"]["schemas"]["AuditDataV1"]
    forbidden_key_pattern = audit_data["propertyNames"]["not"]["pattern"]
    for forbidden in ("nonce", "binding", "session_digest", "csrf_token_digest", "origin_hash"):
        assert forbidden in forbidden_key_pattern

    body_routes = {
        (path, method)
        for path, path_item in paths.items()
        for method, operation in path_item.items()
        if isinstance(operation, dict) and "requestBody" in operation
    }
    assert body_routes == {
        ("/api/v1/commands/evidence-snapshots", "post"),
        ("/api/v1/session/login", "post"),
        ("/api/v1/analysis-runs", "post"),
        ("/api/v1/paper-approvals", "post"),
        ("/api/v1/paper-approvals/{approval_id}/revocations", "post"),
        ("/api/v1/paper-orders/{order_id}/cancel", "post"),
        ("/api/v1/kill-switch/activate", "post"),
        ("/api/v1/kill-switch/recover", "post"),
    }
    for path, method in body_routes:
        assert paths[path][method]["responses"]["422"] == {
            "$ref": "#/components/responses/Phase7ErrorResponse"
        }

    approval_command = document["components"]["schemas"]["ApprovalCommandV1"]
    assert approval_command["properties"]["proposal_id"] == {"$ref": "#/components/schemas/HashV1"}
    recovery_command = document["components"]["schemas"]["KillRecoveryCommandV1"]
    assert recovery_command["properties"]["activation_event_id"] == {
        "$ref": "#/components/schemas/HashV1"
    }
    assert recovery_command["properties"]["incident_reference"]["maxLength"] == 128
    audit_parameters = paths["/api/v1/audit-events"]["get"]["parameters"]
    assert audit_parameters == [
        {
            "name": "after",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "minimum": 0, "default": 0},
        }
    ]
    assert paths["/api/v1/audit-events"]["get"]["responses"]["422"] == {
        "$ref": "#/components/responses/Phase7ErrorResponse"
    }
