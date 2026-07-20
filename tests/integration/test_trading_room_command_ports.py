import asyncio
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

import httpx
import psycopg

from control_api.app import create_app
from control_api.command_ports import (
    AgentAnalysisResult,
    ApprovalDecisionCommand,
    ApprovalRevocationCommand,
    CommandPortRejected,
    KillActivationCommand,
    KillRecoveryCommand,
    PaperCancellationCommand,
    PostgresRiskCommandPort,
    PortResult,
    RiskEvaluationPortResult,
)
from control_api.dependencies import DependencySnapshot, StaticHealthProbe
from control_api.security import LocalOperatorSecurity, make_password_verifier
from control_api.trading_room import (
    PaperWorkerReadinessAuthority,
    PostgresTradingRoom,
    canonical_hash,
)


ORIGIN = "https://localhost:3443"
ENV = {
    "TRADING_MODE": "paper",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
    "REDIS_URL": "redis://127.0.0.1:6380/0",
}


def test_postgres_error_context_cannot_spoof_idempotency_conflict() -> None:
    error = psycopg.errors.AmbiguousColumn(
        'column reference "response" is ambiguous\nCONTEXT: p_idempotency_key'
    )

    rejected = PostgresRiskCommandPort._database_rejection(error)

    assert rejected.code == "RISK_COMMAND_REJECTED"


@dataclass
class DurableRiskPort:
    def __post_init__(self) -> None:
        self.lock = RLock()
        self.receipts: dict[str, tuple[str, dict[str, object]]] = {}
        self.revoked: set[str] = set()
        self.decision_effects = 0

    def decide_approval(self, command: ApprovalDecisionCommand) -> PortResult:
        with self.lock:
            prior = self.receipts.get(command.idempotency_key)
            if prior is not None:
                if prior[0] != command.request_hash:
                    raise CommandPortRejected("IDEMPOTENCY_CONFLICT", "command body changed")
                return PortResult(False, prior[1])
            self.decision_effects += 1
            approval_id = canonical_hash(["approval", command.idempotency_key])
            approval = {
                "approval_id": approval_id,
                "decision": "APPROVED" if command.decision == "APPROVE" else "REJECTED",
            }
            response: dict[str, object] = {
                "result": "REJECTED" if command.decision == "REJECT" else "AUTHORIZATION_ISSUED",
                "approval": approval,
            }
            if command.decision == "APPROVE":
                response["authorization"] = {
                    "authorization_id": canonical_hash(["authorization", approval_id])
                }
            self.receipts[command.idempotency_key] = (command.request_hash, response)
            return PortResult(True, response)

    def revoke_approval(self, command: ApprovalRevocationCommand) -> PortResult:
        with self.lock:
            created = command.approval_id not in self.revoked
            self.revoked.add(command.approval_id)
            return PortResult(
                created,
                {
                    "result": "REVOKED" if created else "ALREADY_REVOKED",
                    "approval_id": command.approval_id,
                },
            )

    def activate_kill(self, command: KillActivationCommand) -> PortResult:
        del command
        raise AssertionError("not used")

    def recover_kill(self, command: KillRecoveryCommand) -> PortResult:
        del command
        raise AssertionError("not used")


@dataclass
class DurablePaperPort:
    risk: DurableRiskPort

    def __post_init__(self) -> None:
        self.lock = RLock()
        self.receipts: dict[str, tuple[str, dict[str, object]]] = {}
        self.cancel_receipts: dict[str, tuple[str, dict[str, object]]] = {}
        self.effects = 0
        self.cancel_effects = 0

    def attempt_authorization(self, authorization_id: str) -> PortResult:
        with self.lock:
            prior = self.receipts.get(authorization_id)
            if prior is not None:
                return PortResult(False, prior[1])
            approval_id = canonical_hash(["approval", next(iter(self.risk.receipts))])
            if approval_id in self.risk.revoked:
                response: dict[str, object] = {
                    "result": "BLOCKED",
                    "attempt": {"reason_code": "AUTHORIZATION_REVOKED"},
                }
            else:
                self.effects += 1
                response = {
                    "result": "CONSUMED_ORDER_CREATED",
                    "order": {"order_id": canonical_hash(["order", authorization_id])},
                }
            self.receipts[authorization_id] = (authorization_id, response)
            return PortResult(True, response)

    def consume_kill_activation(
        self, activation_event_id: str, payload_hash: str, observed_at: object
    ) -> tuple[str, ...]:
        del activation_event_id, payload_hash, observed_at
        raise AssertionError("not used")

    def cancel_order(self, command: PaperCancellationCommand) -> PortResult:
        with self.lock:
            prior = self.cancel_receipts.get(command.idempotency_key)
            if prior is not None:
                if prior[0] != command.request_hash:
                    raise CommandPortRejected("IDEMPOTENCY_CONFLICT", "command body changed")
                return PortResult(False, prior[1])
            self.cancel_effects += 1
            response: dict[str, object] = {
                "result": "ORDER_CANCELLED",
                "order_id": command.order_id,
                "status": "CANCELLED",
                "version": command.expected_version + 1,
            }
            self.cancel_receipts[command.idempotency_key] = (
                command.request_hash,
                response,
            )
            return PortResult(True, response)


class RunningWorkerReadinessAuthority:
    def snapshot(self) -> dict[str, object]:
        return {"status": "HEALTHY", "ready": True}


class UnavailableWorkerReadinessAuthority:
    def snapshot(self) -> dict[str, object]:
        raise psycopg.OperationalError("worker state projection unavailable")


def build(
    risk: DurableRiskPort,
    paper: DurablePaperPort,
    *,
    after_authorization_issued: object | None = None,
    worker_readiness: PaperWorkerReadinessAuthority | None = None,
) -> object:
    room = PostgresTradingRoom(
        "postgresql://control.invalid/woozoo",
        risk_commands=risk,
        paper_commands=paper,
        worker_readiness=worker_readiness or RunningWorkerReadinessAuthority(),
        after_authorization_issued=after_authorization_issued,  # type: ignore[arg-type]
    )
    return create_app(
        environment=ENV,
        probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy")),
        operator_security=LocalOperatorSecurity(
            make_password_verifier("paper-only-password"), ORIGIN
        ),
        trading_room=room,
    )


async def login(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/session/login",
        headers={"Origin": ORIGIN},
        json={"password": "paper-only-password"},
    )
    assert response.status_code == 200


async def csrf(client: httpx.AsyncClient) -> str:
    return (await client.get("/api/v1/session")).json()["csrf_token"]


def approval_headers(token: str) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": token,
        "Idempotency-Key": "durable-approval-1",
        "If-Match": "1",
    }


APPROVAL_BODY = {
    "proposal_id": "1" * 64,
    "decision": "APPROVE",
    "expected_version": 1,
    "paper_order_preview_hash": "2" * 64,
    "reason": "operator reviewed the exact Paper preview",
}


def test_ack_loss_restart_replays_durable_receipts_without_second_paper_effect() -> None:
    risk = DurableRiskPort()
    paper = DurablePaperPort(risk)

    async def scenario() -> None:
        first_app = build(risk, paper)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app), base_url=ORIGIN
        ) as first:
            await login(first)
            created = await first.post(
                "/api/v1/paper-approvals",
                headers=approval_headers(await csrf(first)),
                json=APPROVAL_BODY,
            )
            assert created.status_code == 201

        restarted_app = build(risk, paper)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted_app), base_url=ORIGIN
        ) as restarted:
            await login(restarted)
            replay = await restarted.post(
                "/api/v1/paper-approvals",
                headers=approval_headers(await csrf(restarted)),
                json=APPROVAL_BODY,
            )
            assert replay.status_code == 200
            assert replay.json() == created.json()

        assert risk.decision_effects == 1
        assert paper.effects == 0
        authorization_id = canonical_hash(
            ["authorization", canonical_hash(["approval", "durable-approval-1"])]
        )
        first_attempt = paper.attempt_authorization(authorization_id)
        replayed_attempt = paper.attempt_authorization(authorization_id)
        assert first_attempt.created is True
        assert replayed_attempt.created is False
        assert first_attempt.response == replayed_attempt.response
        assert paper.effects == 1

    asyncio.run(scenario())


def test_concurrent_same_request_issues_once_without_browser_paper_effect() -> None:
    risk = DurableRiskPort()
    paper = DurablePaperPort(risk)
    app = build(risk, paper)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            await login(client)
            first_csrf, second_csrf = await asyncio.gather(csrf(client), csrf(client))
            first, second = await asyncio.gather(
                client.post(
                    "/api/v1/paper-approvals",
                    headers=approval_headers(first_csrf),
                    json=APPROVAL_BODY,
                ),
                client.post(
                    "/api/v1/paper-approvals",
                    headers=approval_headers(second_csrf),
                    json=APPROVAL_BODY,
                ),
            )
            assert {first.status_code, second.status_code} == {200, 201}
            assert first.json() == second.json()
            assert first.json()["result"] == "AUTHORIZATION_ISSUED"
            assert first.json()["authorization_status"] == "ISSUED"
            assert risk.decision_effects == 1
            assert paper.effects == 0

    asyncio.run(scenario())


def test_revocation_can_win_after_authorization_issue_before_first_paper_attempt() -> None:
    risk = DurableRiskPort()
    paper = DurablePaperPort(risk)

    def revoke_before_attempt(_authorization_id: str) -> None:
        approval_id = canonical_hash(["approval", "durable-approval-1"])
        risk.revoked.add(approval_id)

    app = build(risk, paper, after_authorization_issued=revoke_before_attempt)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            await login(client)
            issued = await client.post(
                "/api/v1/paper-approvals",
                headers=approval_headers(await csrf(client)),
                json=APPROVAL_BODY,
            )
            assert issued.status_code == 201
            assert issued.json()["result"] == "AUTHORIZATION_ISSUED"
            assert issued.json()["authorization_status"] == "ISSUED"
            assert issued.json()["order_id"] is None
            assert paper.effects == 0
            authorization_id = canonical_hash(
                ["authorization", canonical_hash(["approval", "durable-approval-1"])]
            )
            blocked = paper.attempt_authorization(authorization_id)
            assert blocked.response["result"] == "BLOCKED"
            assert blocked.response["attempt"] == {"reason_code": "AUTHORIZATION_REVOKED"}
            assert paper.effects == 0

    asyncio.run(scenario())


def test_missing_dedicated_command_adapters_returns_503_fail_closed() -> None:
    app = create_app(
        environment=ENV,
        probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy")),
        operator_security=LocalOperatorSecurity(
            make_password_verifier("paper-only-password"), ORIGIN
        ),
        trading_room=PostgresTradingRoom(
            "postgresql://control.invalid/woozoo",
            worker_readiness=RunningWorkerReadinessAuthority(),
        ),
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            await login(client)
            response = await client.post(
                "/api/v1/paper-approvals",
                headers=approval_headers(await csrf(client)),
                json=APPROVAL_BODY,
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "PRODUCTION_AUTHORITY_UNAVAILABLE"

    asyncio.run(scenario())


def test_unavailable_worker_readiness_blocks_before_risk_command() -> None:
    risk = DurableRiskPort()
    paper = DurablePaperPort(risk)
    app = build(risk, paper, worker_readiness=UnavailableWorkerReadinessAuthority())

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            await login(client)
            response = await client.post(
                "/api/v1/paper-approvals",
                headers=approval_headers(await csrf(client)),
                json=APPROVAL_BODY,
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "PAPER_WORKER_STATE_UNAVAILABLE"
            assert risk.decision_effects == 0
            assert paper.effects == 0

    asyncio.run(scenario())


def test_malformed_preconditions_have_zero_effect_and_do_not_consume_csrf() -> None:
    risk = DurableRiskPort()
    paper = DurablePaperPort(risk)
    app = build(risk, paper)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            await login(client)
            token = await csrf(client)
            bad_headers = approval_headers(token)
            bad_headers["If-Match"] = "2"
            rejected = await client.post(
                "/api/v1/paper-approvals", headers=bad_headers, json=APPROVAL_BODY
            )
            assert rejected.status_code == 412
            accepted = await client.post(
                "/api/v1/paper-approvals",
                headers=approval_headers(token),
                json=APPROVAL_BODY,
            )
            assert accepted.status_code == 201
            assert risk.decision_effects == 1
            assert paper.effects == 0

    asyncio.run(scenario())


def test_paper_cancel_is_guarded_and_replays_durable_paper_receipt() -> None:
    risk = DurableRiskPort()
    paper = DurablePaperPort(risk)
    app = build(risk, paper)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            await login(client)
            headers = {
                "Origin": ORIGIN,
                "X-CSRF-Token": await csrf(client),
                "Idempotency-Key": "durable-cancel-1",
                "If-Match": "1",
            }
            first = await client.post(
                "/api/v1/paper-orders/" + "4" * 64 + "/cancel",
                headers=headers,
                json={"expected_version": 1, "reason": "operator cancelled Paper order"},
            )
            assert first.status_code == 201
            replay_headers = dict(headers)
            replay_headers["X-CSRF-Token"] = await csrf(client)
            replay = await client.post(
                "/api/v1/paper-orders/" + "4" * 64 + "/cancel",
                headers=replay_headers,
                json={"expected_version": 1, "reason": "operator cancelled Paper order"},
            )
            assert replay.status_code == 200
            assert replay.json() == first.json()
            assert paper.cancel_effects == 1

    asyncio.run(scenario())


class FakeAgentAnalysisPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, str, str]] = []

    def analyze_latest(
        self,
        symbol: str,
        observed_at: datetime,
        idempotency_key: str,
        request_hash: str,
    ) -> AgentAnalysisResult:
        self.calls.append((symbol, observed_at, idempotency_key, request_hash))
        return AgentAnalysisResult(
            created=len(self.calls) == 1,
            run_id="5" * 64,
            proposal_id="6" * 64,
            outcome="COMPLETED",
        )


class FakeRiskEvaluationPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, datetime]] = []

    def evaluate_proposal(
        self, proposal_id: str, paper_account_id: str, recorded_at: datetime
    ) -> RiskEvaluationPortResult:
        self.calls.append((proposal_id, paper_account_id, recorded_at))
        return RiskEvaluationPortResult(
            created=len(self.calls) == 1,
            decision_id="7" * 64,
        )


class AnalysisProjectionRoom(PostgresTradingRoom):
    def get_analysis(self, run_id: str) -> dict[str, object]:
        return {
            "schema_version": "woozoo.analysis-run-view/v1",
            "namespace": "paper",
            "symbol": "BTCUSDT",
            "evidence_id": "8" * 64,
            "provider": "mock",
            "tool_count": 0,
            "run_id": run_id,
            "status": "COMPLETED",
            "report": {
                "summary": "저장된 모의 언어 모델의 모의투자 분석",
                "confidence": "0.50",
                "hold_reasons": [],
            },
            "proposal_id": "6" * 64,
            "risk_decision_id": "7" * 64,
        }


def test_analysis_composes_ids_time_only_ports_and_retries_partial_authorities() -> None:
    agent = FakeAgentAnalysisPort()
    risk = FakeRiskEvaluationPort()
    room = AnalysisProjectionRoom(
        "postgresql://control.invalid/woozoo",
        agent_analysis=agent,
        risk_evaluation=risk,
    )

    created = room.create_analysis("BTCUSDT", "analysis-command-1")
    replay = room.create_analysis("BTCUSDT", "analysis-command-1")

    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.body == created.body
    assert [call[0] for call in agent.calls] == ["BTCUSDT", "BTCUSDT"]
    assert [call[2] for call in agent.calls] == ["analysis-command-1", "analysis-command-1"]
    assert len({call[3] for call in agent.calls}) == 1
    assert all(len(call[3]) == 64 for call in agent.calls)
    assert {call[0] for call in risk.calls} == {"6" * 64}
    assert {call[1] for call in risk.calls} == {
        "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"
    }
    assert all(call[1].tzinfo is not None for call in agent.calls)
    assert all(call[2].tzinfo is not None for call in risk.calls)
