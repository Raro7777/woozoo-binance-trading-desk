from __future__ import annotations

import asyncio

import httpx

from control_api.app import create_app
from control_api.dependencies import DependencySnapshot, StaticHealthProbe
from control_api.security import LocalOperatorSecurity, make_password_verifier
from control_api.testnet_operator import InMemoryTestnetOperatorRoom
from control_api.trading_room import InMemoryTradingRoom


ORIGIN = "https://localhost:3443"
ENV = {
    "TRADING_MODE": "paper",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
    "REDIS_URL": "redis://127.0.0.1:6380/0",
}
PROPOSAL_ID = "a" * 64


def build(*, ready: bool = False) -> tuple[object, InMemoryTestnetOperatorRoom]:
    testnet = InMemoryTestnetOperatorRoom(ready=ready)
    app = create_app(
        environment=ENV,
        probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy")),
        operator_security=LocalOperatorSecurity(
            make_password_verifier("paper-only-password"), ORIGIN
        ),
        trading_room=InMemoryTradingRoom(),
        testnet_room=testnet,
    )
    return app, testnet


async def login(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/session/login",
        headers={"Origin": ORIGIN},
        json={"password": "paper-only-password"},
    )
    assert response.status_code == 200
    session = await client.get("/api/v1/session")
    return session.json()["csrf_token"]


def test_testnet_routes_are_authenticated_sanitized_and_default_off() -> None:
    app, room = build()

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            denied = await client.get("/api/v1/testnet/operator-state")
            assert denied.status_code == 401
            csrf = await login(client)
            state = await client.get("/api/v1/testnet/operator-state")
            assert state.status_code == 200
            assert state.headers["cache-control"] == "no-store"
            body = state.json()
            assert body["environment"] == "BINANCE_SPOT_TESTNET"
            assert body["activation_status"] == "DISABLED"
            assert body["gateway_health"] == "DISABLED"
            assert body["new_commands_allowed"] is False
            assert all(
                marker not in state.text.lower()
                for marker in ("https://", "api_key", "secret", "signature", "balance")
            )

            invalid = await client.post(
                "/api/v1/testnet-approvals",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "unsafe-extra-fields",
                    "If-Match": "1",
                },
                json={
                    "proposal_id": PROPOSAL_ID,
                    "decision": "APPROVE",
                    "expected_version": 1,
                    "testnet_order_preview_digest": "b" * 64,
                    "approval_input_digest": "c" * 64,
                    "reason": "추가 금융 필드는 허용하지 않음",
                    "quantity": "999",
                },
            )
            assert invalid.status_code == 422
            assert room.intent_count == 0

    asyncio.run(scenario())


def test_ready_testnet_approval_records_intent_only_and_is_idempotent() -> None:
    app, room = build(ready=True)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            csrf = await login(client)
            view_response = await client.get(
                f"/api/v1/proposals/{PROPOSAL_ID}/testnet-approval-view"
            )
            assert view_response.status_code == 200
            view = view_response.json()
            assert view["environment"] == "BINANCE_SPOT_TESTNET"
            assert view["approve_action_allowed"] is True
            payload = {
                "proposal_id": PROPOSAL_ID,
                "decision": "APPROVE",
                "expected_version": view["view_version"],
                "testnet_order_preview_digest": view["testnet_order_preview_digest"],
                "approval_input_digest": view["approval_input_digest"],
                "reason": "표시된 Testnet 주문을 별도로 승인",
            }
            headers = {
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "testnet-approval-intent-1",
                "If-Match": str(view["view_version"]),
            }
            created = await client.post("/api/v1/testnet-approvals", headers=headers, json=payload)
            assert created.status_code == 201
            assert created.json()["result"] == "AUTHORIZATION_ISSUED"
            assert room.intent_count == 1
            assert room.gateway_call_count == 0

            csrf = (await client.get("/api/v1/session")).json()["csrf_token"]
            replay = await client.post(
                "/api/v1/testnet-approvals",
                headers={**headers, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert replay.status_code == 200
            assert replay.json() == created.json()
            assert room.intent_count == 1
            assert room.gateway_call_count == 0

    asyncio.run(scenario())
