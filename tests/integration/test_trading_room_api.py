import asyncio
import json
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from control_api.app import create_app
from control_api.dependencies import DependencySnapshot, StaticHealthProbe
from control_api.security import COOKIE_NAME, LocalOperatorSecurity, make_password_verifier
from control_api.trading_room import InMemoryTradingRoom


ORIGIN = "https://localhost:3443"
ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "packages/contracts/spec"
OPENAPI = json.loads((SPEC / "openapi.v1.json").read_text("utf-8"))
ENV = {
    "TRADING_MODE": "paper",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
    "REDIS_URL": "redis://127.0.0.1:6380/0",
}


def build(
    room: InMemoryTradingRoom | None = None,
) -> tuple[object, InMemoryTradingRoom]:
    room = room or InMemoryTradingRoom()
    app = create_app(
        environment=ENV,
        probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy")),
        operator_security=LocalOperatorSecurity(
            make_password_verifier("paper-only-password"), ORIGIN
        ),
        trading_room=room,
    )
    return app, room


class AuditAuthoritySpy(InMemoryTradingRoom):
    def __init__(self) -> None:
        super().__init__()
        self.audit_calls: list[int] = []

    def audit_events(self, after: int = 0) -> list[dict[str, object]]:
        self.audit_calls.append(after)
        return super().audit_events(after)


def validate_contract(name: str, value: object) -> None:
    schema = json.loads((SPEC / name).read_text("utf-8"))
    risk_v1 = json.loads((SPEC / "risk-input.v1.json").read_text("utf-8"))
    registry = Registry().with_resource(
        "https://schemas.woozoo.local/risk-input/v1", Resource.from_contents(risk_v1)
    )
    Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(value)


def validate_openapi_response(schema_name: str, value: object) -> None:
    uri = "https://schemas.woozoo.local/openapi/v1"
    registry = Registry().with_resource(
        uri,
        Resource.from_contents(OPENAPI, default_specification=DRAFT202012),
    )
    Draft202012Validator(
        {"$ref": f"{uri}#/components/schemas/{schema_name}"},
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(value)


async def login(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/session/login",
        headers={"Origin": ORIGIN},
        json={"password": "paper-only-password"},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    response = await client.get("/api/v1/session")
    assert response.headers["cache-control"] == "no-store"
    validate_contract("local-session.v1.json", response.json())
    return response.json()["csrf_token"]


def test_e2e_001_authenticated_proposal_approval_creates_exactly_one_paper_order() -> None:
    app, _room = build()

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            oversized_secret = "phase7-never-echo-" + "x" * 1024
            invalid = await client.post(
                "/api/v1/session/login",
                headers={"Origin": ORIGIN, "X-Hostile-Context": oversized_secret},
                json={"password": oversized_secret},
            )
            assert invalid.status_code == 422
            assert invalid.json() == {
                "error": {
                    "code": "REQUEST_VALIDATION_FAILED",
                    "message": "request validation failed",
                }
            }
            assert oversized_secret not in invalid.text
            assert all(
                forbidden not in invalid.text.lower()
                for forbidden in ("password", "input", "headers", "context")
            )
            invalid_proposal = await client.post(
                "/api/v1/paper-approvals",
                json={
                    "proposal_id": "not-a-hash",
                    "decision": "REJECT",
                    "expected_version": 1,
                    "paper_order_preview_hash": "1" * 64,
                    "reason": "closed identifier validation",
                },
            )
            assert invalid_proposal.status_code == 422
            valid_proposal_boundary = await client.post(
                "/api/v1/paper-approvals",
                json={
                    "proposal_id": "a" * 64,
                    "decision": "REJECT",
                    "expected_version": 1,
                    "paper_order_preview_hash": "1" * 64,
                    "reason": "closed identifier validation",
                },
            )
            assert valid_proposal_boundary.status_code == 400
            valid_incident_boundary = await client.post(
                "/api/v1/kill-switch/recover",
                json={
                    "expected_version": 1,
                    "activation_event_id": "1" * 64,
                    "incident_reference": "i" * 128,
                    "reason": "closed incident reference boundary",
                },
            )
            assert valid_incident_boundary.status_code == 400
            invalid_activation_hash = await client.post(
                "/api/v1/kill-switch/recover",
                json={
                    "expected_version": 1,
                    "activation_event_id": "A" * 64,
                    "incident_reference": "incident-activation-hash",
                    "reason": "activation identifiers are lowercase hashes",
                },
            )
            assert invalid_activation_hash.status_code == 422
            assert invalid_activation_hash.json() == {
                "error": {
                    "code": "REQUEST_VALIDATION_FAILED",
                    "message": "request validation failed",
                }
            }
            assert "activation_event_id" not in invalid_activation_hash.text
            assert "A" * 64 not in invalid_activation_hash.text
            invalid_incident_boundary = await client.post(
                "/api/v1/kill-switch/recover",
                json={
                    "expected_version": 1,
                    "activation_event_id": "1" * 64,
                    "incident_reference": "i" * 129,
                    "reason": "closed incident reference boundary",
                },
            )
            assert invalid_incident_boundary.status_code == 422
            csrf = await login(client)
            created = await client.post(
                "/api/v1/analysis-runs",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "analysis-001",
                },
                json={"symbol": "BTCUSDT"},
            )
            assert created.status_code == 201
            validate_contract("analysis-run-view.v1.json", created.json())
            proposal_id = created.json()["proposal_id"]
            risk_response = await client.get(
                f"/api/v1/risk-decisions/{created.json()['risk_decision_id']}"
            )
            validate_contract("risk-decision.v1.json", risk_response.json())

            view = await client.get(f"/api/v1/proposals/{proposal_id}/approval-view")
            assert view.json()["status"] == "READY"
            assert view.json()["approval_id"] is None
            validate_contract("approval-view.v1.json", view.json())
            preview_hash = view.json()["paper_order_preview_hash"]

            csrf = (await client.get("/api/v1/session")).json()["csrf_token"]
            approved = await client.post(
                "/api/v1/paper-approvals",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "approval-001",
                    "If-Match": "1",
                },
                json={
                    "proposal_id": proposal_id,
                    "decision": "APPROVE",
                    "expected_version": 1,
                    "paper_order_preview_hash": preview_hash,
                    "reason": "operator reviewed the Paper proposal",
                },
            )
            assert approved.status_code == 201
            assert approved.json()["result"] == "CONSUMED_ORDER_CREATED"
            assert approved.json()["approval_status"] == "APPROVED"
            assert approved.json()["authorization_status"] == "CONSUMED"
            assert len(approved.json()["order_id"]) == 64
            validate_openapi_response("ApprovalCommandReceiptV1", approved.json())

            replay_csrf = (await client.get("/api/v1/session")).json()["csrf_token"]
            replay = await client.post(
                "/api/v1/paper-approvals",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": replay_csrf,
                    "Idempotency-Key": "approval-001",
                    "If-Match": "1",
                },
                json={
                    "proposal_id": proposal_id,
                    "decision": "APPROVE",
                    "expected_version": 1,
                    "paper_order_preview_hash": preview_hash,
                    "reason": "operator reviewed the Paper proposal",
                },
            )
            assert replay.json() == approved.json()
            portfolio = await client.get("/api/v1/paper-portfolio")
            assert len(portfolio.json()["orders"]) == 1
            validate_openapi_response("PaperPortfolioViewV1", portfolio.json())
            dashboard = await client.get("/api/v1/trading-room")
            validate_openapi_response("TradingRoomDashboardV1", dashboard.json())
            kill = await client.get("/api/v1/kill-switch")
            validate_openapi_response("KillSwitchViewV1", kill.json())

    asyncio.run(scenario())


def test_phase7_domain_records_match_frozen_approval_authorization_and_order_contracts() -> None:
    room = InMemoryTradingRoom()
    created = room.create_analysis("BTCUSDT", "contract-domain-run")
    proposal_id = str(created.body["proposal_id"])
    view = room.approval_view(proposal_id)
    result = room.decide_approval(
        proposal_id=proposal_id,
        decision="APPROVE",
        expected_version=1,
        preview_hash=str(view["paper_order_preview_hash"]),
        idempotency_key="contract-domain-approval",
        reason="contract validation",
    )
    approval = result.body["approval"]
    authorization = result.body["authorization"]
    assert isinstance(approval, dict)
    assert isinstance(authorization, dict)
    assert approval["approval_nonce"] != authorization["authorization_nonce"]
    validate_contract("paper-approval.v1.json", approval)
    validate_contract("paper-execution-authorization.v1.json", authorization)
    validate_contract("paper-order.v2.json", result.body["order"])


def test_e2e_002_stale_data_consumes_csrf_and_blocks_without_order() -> None:
    app, room = build()

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            csrf = await login(client)
            created = await client.post(
                "/api/v1/analysis-runs",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "analysis-before-stale",
                },
                json={"symbol": "BTCUSDT"},
            )
            proposal_id = created.json()["proposal_id"]
            view = (await client.get(f"/api/v1/proposals/{proposal_id}/approval-view")).json()
            assert view["status"] == "READY"
            room.set_guard_state(data_quality="STALE")
            csrf = (await client.get("/api/v1/session")).json()["csrf_token"]
            blocked = await client.post(
                "/api/v1/paper-approvals",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "approval-after-stale",
                    "If-Match": "1",
                },
                json={
                    "proposal_id": proposal_id,
                    "decision": "APPROVE",
                    "expected_version": 1,
                    "paper_order_preview_hash": view["paper_order_preview_hash"],
                    "reason": "operator clicked after data became stale",
                },
            )
            assert blocked.status_code == 409
            assert blocked.json()["error"]["code"] == "APPROVAL_NOT_READY"
            replay = await client.post(
                "/api/v1/paper-approvals",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "approval-after-stale",
                    "If-Match": "1",
                },
                json={
                    "proposal_id": proposal_id,
                    "decision": "APPROVE",
                    "expected_version": 1,
                    "paper_order_preview_hash": view["paper_order_preview_hash"],
                    "reason": "operator clicked after data became stale",
                },
            )
            assert replay.status_code == 403
            assert (await client.get("/api/v1/paper-portfolio")).json()["orders"] == []

    asyncio.run(scenario())


def test_e2e_004_kill_cancels_open_order_and_manual_recovery_is_audited() -> None:
    app, _room = build()

    async def command(
        client: httpx.AsyncClient, path: str, body: dict[str, object]
    ) -> httpx.Response:
        csrf = (await client.get("/api/v1/session")).json()["csrf_token"]
        return await client.post(
            path,
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"command-{path.rsplit('/', 1)[-1]}",
                "If-Match": str(body["expected_version"]),
            },
            json=body,
        )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            csrf = await login(client)
            run = await client.post(
                "/api/v1/analysis-runs",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": "kill-run"},
                json={"symbol": "ETHUSDT"},
            )
            view = (
                await client.get(f"/api/v1/proposals/{run.json()['proposal_id']}/approval-view")
            ).json()
            csrf = (await client.get("/api/v1/session")).json()["csrf_token"]
            await client.post(
                "/api/v1/paper-approvals",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "kill-approval",
                    "If-Match": "1",
                },
                json={
                    "proposal_id": run.json()["proposal_id"],
                    "decision": "APPROVE",
                    "expected_version": 1,
                    "paper_order_preview_hash": view["paper_order_preview_hash"],
                    "reason": "Paper-only review",
                },
            )
            activated = await command(
                client,
                "/api/v1/kill-switch/activate",
                {"expected_version": 0, "reason": "operator incident drill"},
            )
            assert activated.status_code == 201
            assert len(activated.json()["cancelled_order_ids"]) == 1
            kill = activated.json()["kill_switch"]
            recovered = await command(
                client,
                "/api/v1/kill-switch/recover",
                {
                    "expected_version": kill["version"],
                    "activation_event_id": kill["activation_event_id"],
                    "incident_reference": "incident/local-drill-001",
                    "reason": "health and reconciliation reviewed",
                },
            )
            assert recovered.status_code == 201
            assert recovered.json()["kill_switch"]["active"] is False
            events = (await client.get("/api/v1/audit-events")).json()["events"]
            assert {item["event_type"] for item in events} >= {
                "KILL_SWITCH_ACTIVATED",
                "PAPER_ORDER_CANCELLED_BY_KILL",
                "KILL_SWITCH_RECOVERED",
            }

    asyncio.run(scenario())


def test_audit_api_preserves_provenance_and_recursively_redacts_command_secrets() -> None:
    app, room = build()
    room._audit_event(  # noqa: SLF001 - exercises the read boundary with hostile persisted data
        "RISK_TEST_EVENT",
        {
            "visible": "kept",
            "nested": {
                "approval_nonce": "must-not-leave-the-api",
                "session_binding_hash": "must-not-leave-the-api",
                "items": [{"origin_hash": "must-not-leave-the-api", "safe": True}],
            },
        },
        producer="risk-engine",
        actor_id=None,
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            await login(client)
            first_page = (await client.get("/api/v1/audit-events")).json()
            validate_openapi_response("AuditPageV1", first_page)
            event = first_page["events"][-1]
            assert event["producer"] == "risk-engine"
            assert event["actor_id"] is None
            assert event["data"] == {"visible": "kept", "nested": {"items": [{"safe": True}]}}
            empty_page = (
                await client.get(
                    "/api/v1/audit-events", params={"after": first_page["next_cursor"]}
                )
            ).json()
            assert empty_page == {"events": [], "next_cursor": first_page["next_cursor"]}

    asyncio.run(scenario())


def test_unauthenticated_audit_request_never_calls_audit_authority() -> None:
    room = AuditAuthoritySpy()
    app, _ = build(room)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            response = await client.get("/api/v1/audit-events")

            assert response.status_code == 401
            assert room.audit_calls == []

    asyncio.run(scenario())


def test_authenticated_negative_audit_cursor_is_sanitized_and_not_dispatched() -> None:
    room = AuditAuthoritySpy()
    app, _ = build(room)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            await login(client)
            response = await client.get("/api/v1/audit-events", params={"after": -1})

            assert response.status_code == 422
            assert response.json() == {
                "error": {
                    "code": "REQUEST_VALIDATION_FAILED",
                    "message": "request validation failed",
                }
            }
            assert "after" not in response.text
            assert room.audit_calls == []

    asyncio.run(scenario())
