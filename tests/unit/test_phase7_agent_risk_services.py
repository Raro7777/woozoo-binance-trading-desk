from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime

from agent_orchestrator import (
    AgentAnalysisService,
    AgentWorkflow,
    AnalysisCommand,
    EvidenceContext,
    MockLlmProvider,
    bind_paper_risk_input,
)
from agent_orchestrator.persistence import AnalysisCommandReceipt, PersistedAnalysis
from risk_engine import (
    RiskDecisionService,
    RiskEvaluationCommand,
    RiskProposalCommand,
    evaluate_risk,
)
from risk_engine.persistence import PersistedRiskDecision, _assemble_risk_input
from test_risk_engine import risk_input


NOW = "2026-07-20T00:00:00Z"


def evidence(**changes: object) -> EvidenceContext:
    values: dict[str, object] = {
        "evidence_id": "e" * 64,
        "evidence_digest": "a" * 64,
        "symbol": "BTCUSDT",
        "as_of": NOW,
        "knowledge_cutoff": NOW,
        "quality": "healthy",
        "item_ids": ("1" * 64,),
        "quoted_content": ("immutable public market observation",),
    }
    values.update(changes)
    return EvidenceContext(**values)  # type: ignore[arg-type]


class FakeAgentStore:
    def __init__(self, value: EvidenceContext) -> None:
        self.value = value
        self.persisted = []

    def load_evidence(self, evidence_id: str) -> EvidenceContext | None:
        return self.value if evidence_id == self.value.evidence_id else None

    def persist(self, result):  # type: ignore[no-untyped-def]
        self.persisted.append(result)
        return PersistedAnalysis(
            True,
            result.run["run_id"],
            result.run["proposal_id"],
            "f" * 64,
            result.run["outcome"],
            result.run["hold_reason"],
        )


class ReceiptWinnerAgentStore(FakeAgentStore):
    def __init__(self, value: EvidenceContext, winner_outcome: str) -> None:
        super().__init__(value)
        self.winner_outcome = winner_outcome

    def persist(
        self,
        result,  # type: ignore[no-untyped-def]
        *,
        command_receipt: AnalysisCommandReceipt | None = None,
    ) -> PersistedAnalysis:
        assert command_receipt is not None
        self.persisted.append(result)
        return PersistedAnalysis(
            created=False,
            run_id="f" * 64,
            proposal_id="d" * 64 if self.winner_outcome == "COMPLETED" else None,
            audit_hash="c" * 64,
            outcome=self.winner_outcome,
            hold_reason=("EVIDENCE_UNHEALTHY" if self.winner_outcome == "HOLD" else None),
        )


class FakeRiskStore:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], object, datetime]] = []
        self.proposal_calls: list[tuple[str, str, datetime]] = []

    def persist_decision(self, risk_input, decision, *, recorded_at):  # type: ignore[no-untyped-def]
        self.calls.append((deepcopy(risk_input), decision, recorded_at))
        return PersistedRiskDecision(
            True,
            "d" * 64,
            decision.risk_input_digest,
            decision.decision_hash,
            "f" * 64,
        )

    def evaluate_proposal(self, proposal_id, paper_account_id, recorded_at):  # type: ignore[no-untyped-def]
        self.proposal_calls.append((proposal_id, paper_account_id, recorded_at))
        payload = paper_risk_input()
        decision = evaluate_risk(payload)
        persisted = PersistedRiskDecision(
            True, "d" * 64, decision.risk_input_digest, decision.decision_hash, "f" * 64
        )
        return payload, decision, persisted


def production_proposal() -> dict[str, object]:
    result = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: NOW, namespace="paper").run(evidence())
    )
    assert result.proposal is not None
    return result.proposal


def paper_risk_input() -> dict[str, object]:
    proposal = production_proposal()
    base = risk_input()
    data = base["data"]
    preview = base["order_preview"]
    assert isinstance(data, dict) and isinstance(preview, dict)
    data["evidence_id"] = proposal["evidence_id"]
    data["evidence_hash"] = proposal["evidence_digest"]
    data["as_of"] = proposal["as_of"]
    data["knowledge_cutoff"] = proposal["knowledge_cutoff"]
    clock = base["decision_clock"]
    assert isinstance(clock, dict)
    clock["decision_as_of"] = proposal["knowledge_cutoff"]
    preview["symbol"] = proposal["symbol"]
    preview["side"] = proposal["side"]
    from platform_core import canonical_hash

    preview["paper_order_preview_hash"] = canonical_hash(
        {key: value for key, value in preview.items() if key != "paper_order_preview_hash"}
    )
    return bind_paper_risk_input(base, proposal)


def test_paper_workflow_has_distinct_restart_stable_identity() -> None:
    test_run = asyncio.run(AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(evidence()))
    first = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: NOW, namespace="paper").run(evidence())
    )
    second = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: NOW, namespace="paper").run(evidence())
    )
    assert first == second
    assert first.run["namespace"] == "paper"
    assert first.run["run_id"] != test_run.run["run_id"]


def test_unhealthy_or_future_evidence_persists_audited_hold_without_proposal() -> None:
    for unsafe in (evidence(quality="stale"), evidence(future_contamination=True)):
        store = FakeAgentStore(unsafe)
        result = AgentAnalysisService(store, clock=lambda: NOW).execute(
            AnalysisCommand(unsafe.evidence_id)
        )
        assert result.status == "HOLD"
        assert result.workflow.proposal is None
        assert result.persisted is not None
        assert len(store.persisted) == 1
        assert store.persisted[0].proposal is None


def test_healthy_analysis_is_restart_idempotent_at_store_boundary() -> None:
    store = FakeAgentStore(evidence())
    service = AgentAnalysisService(store, clock=lambda: NOW)
    first = service.execute(AnalysisCommand("e" * 64))
    second = service.execute(AnalysisCommand("e" * 64))
    assert first.workflow == second.workflow
    assert first.persisted is not None and second.persisted is not None
    assert first.persisted.run_id == second.persisted.run_id


def test_receipt_winner_status_overrides_opposite_local_workflow_outcome() -> None:
    request_hash = "a" * 64
    cases = (
        (evidence(), "HOLD", "EVIDENCE_UNHEALTHY"),
        (evidence(quality="stale"), "COMPLETED", None),
    )
    for selected_evidence, winner_outcome, winner_hold_reason in cases:
        result = AgentAnalysisService(
            ReceiptWinnerAgentStore(selected_evidence, winner_outcome), clock=lambda: NOW
        ).execute(
            AnalysisCommand(
                selected_evidence.evidence_id,
                "concurrent-receipt-winner",
                request_hash,
            )
        )

        assert result.status == winner_outcome
        assert result.hold_reason == winner_hold_reason
        assert result.persisted is not None
        assert result.persisted.outcome == winner_outcome


def test_paper_risk_v3_binds_preview_policy_and_preserves_base() -> None:
    base = risk_input()
    original = deepcopy(base)
    proposal = production_proposal()
    data = base["data"]
    preview = base["order_preview"]
    assert isinstance(data, dict) and isinstance(preview, dict)
    data["evidence_id"] = proposal["evidence_id"]
    data["evidence_hash"] = proposal["evidence_digest"]
    data["as_of"] = proposal["as_of"]
    data["knowledge_cutoff"] = proposal["knowledge_cutoff"]
    clock = base["decision_clock"]
    assert isinstance(clock, dict)
    clock["decision_as_of"] = proposal["knowledge_cutoff"]
    from platform_core import canonical_hash

    preview["paper_order_preview_hash"] = canonical_hash(
        {key: value for key, value in preview.items() if key != "paper_order_preview_hash"}
    )
    bound = bind_paper_risk_input(base, proposal)
    assert bound["risk_input_schema_version"] == "woozoo.risk-input/v3"
    assert bound["namespace"] == "paper"
    assert bound["preview_policy_version"] == "woozoo.paper-order-preview-policy/v1"
    assert "preview_policy_version" not in base
    assert original["risk_input_schema_version"] == base["risk_input_schema_version"]
    assert evaluate_risk(bound).verdict == "ALLOWED"


def test_risk_service_evaluates_authoritatively_before_persisting() -> None:
    store = FakeRiskStore()
    command = RiskEvaluationCommand(paper_risk_input(), datetime(2026, 7, 20, tzinfo=UTC))
    first = RiskDecisionService(store).execute(command)
    second = RiskDecisionService(store).execute(command)
    assert first.decision == second.decision
    assert first.decision.verdict == "ALLOWED"
    assert len(store.calls) == 2


def test_authoritative_context_assembles_closed_fresh_v3_preview() -> None:
    from platform_core import canonical_hash

    proposal = production_proposal()
    reconciliation = {
        "checkpoint_id": "c" * 64,
        "health": "HEALTHY",
        "mismatch_codes": [],
    }
    context: dict[str, object] = {
        "proposal": proposal,
        "proposal_hash": proposal["proposal_hash"],
        "evidence": {
            "evidence_id": proposal["evidence_id"],
            "evidence_digest": proposal["evidence_digest"],
            # PostgreSQL JSONB renders UTC timestamptz with an explicit offset;
            # the Agent payload remains canonically bound to RFC3339 Z.
            "as_of": "2026-07-20T00:00:00+00:00",
            "knowledge_cutoff": "2026-07-20T00:00:00+00:00",
            "quality_status": "healthy",
        },
        "balances": {
            "USDT": {"available": "10000", "held": "0", "version": 0},
            "BTC": {"available": "0", "held": "0", "version": 0},
            "ETH": {"available": "0", "held": "0", "version": 0},
        },
        "books": {
            "BTCUSDT": {
                "best_bid": "99.99",
                "best_ask": "100",
                "event_time": NOW,
                "received_at": NOW,
                "quality_status": "healthy",
            },
            "ETHUSDT": {
                "best_bid": "49.99",
                "best_ask": "50",
                "event_time": NOW,
                "received_at": NOW,
                "quality_status": "healthy",
            },
        },
        "open_orders": [],
        "fifo_lots": [],
        "realized_pnl_24h": "0",
        "prior_high_water": "10000",
        "kill_switch": {"active": False, "version": 0, "event_id": None},
        "reconciliation": {
            **reconciliation,
            "checkpoint_hash": canonical_hash(reconciliation),
        },
        "order_intent_seen": False,
        "recorded_at": NOW,
    }
    assembled = _assemble_risk_input(context)
    preview = assembled["order_preview"]
    assert isinstance(preview, dict)
    assert assembled["risk_input_schema_version"] == "woozoo.risk-input/v3"
    data = assembled["data"]
    assert isinstance(data, dict)
    assert data["as_of"] == NOW and data["knowledge_cutoff"] == NOW
    assert preview["limit_price"] == "100"
    assert preview["quantity"] == "0.24975"
    assert evaluate_risk(assembled).verdict == "ALLOWED"

    boundary_context = deepcopy(context)
    boundary_context["recorded_at"] = "2026-07-20T00:00:05Z"
    boundary = _assemble_risk_input(boundary_context)
    boundary_data = boundary["data"]
    assert isinstance(boundary_data, dict)
    assert boundary_data["freshness"] == "FRESH"

    stale_context = deepcopy(context)
    stale_context["recorded_at"] = "2026-07-20T00:00:05.000001Z"
    stale = _assemble_risk_input(stale_context)
    stale_data = stale["data"]
    assert isinstance(stale_data, dict)
    assert stale_data["freshness"] == "STALE"
    stale_decision = evaluate_risk(stale)
    assert stale_decision.verdict == "DENIED"
    assert "DATA_STALE" in stale_decision.ordered_reason_codes


def test_evaluate_proposal_port_accepts_only_ids_and_server_time() -> None:
    store = FakeRiskStore()
    recorded_at = datetime(2026, 7, 20, tzinfo=UTC)
    result = RiskDecisionService(store).evaluate_proposal(
        RiskProposalCommand("a" * 64, "b" * 64, recorded_at)
    )
    assert result.persisted.decision_id == "d" * 64
    assert result.decision.verdict == "ALLOWED"
    assert store.proposal_calls == [("a" * 64, "b" * 64, recorded_at)]
