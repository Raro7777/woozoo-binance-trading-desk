"""Narrow production command port for deterministic Risk evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .engine import evaluate_risk
from .models import RiskDecision
from .persistence import PersistedRiskDecision, PostgresRiskStore
from .settings import RiskSettings


class RiskStore(Protocol):
    def persist_decision(
        self,
        risk_input: dict[str, object],
        decision: RiskDecision,
        *,
        recorded_at: datetime,
    ) -> PersistedRiskDecision: ...

    def evaluate_proposal(
        self, proposal_id: str, paper_account_id: str, recorded_at: datetime
    ) -> tuple[dict[str, object], RiskDecision, PersistedRiskDecision]: ...


@dataclass(frozen=True, slots=True)
class RiskEvaluationCommand:
    risk_input: dict[str, object]
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class RiskProposalCommand:
    proposal_id: str
    paper_account_id: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class RiskEvaluationResult:
    decision: RiskDecision
    persisted: PersistedRiskDecision


class RiskDecisionService:
    """Evaluate one complete Paper v3 input and atomically persist its outbox."""

    def __init__(self, store: RiskStore) -> None:
        self._store = store

    @classmethod
    def from_env(cls) -> "RiskDecisionService":
        settings = RiskSettings.from_env()
        return cls(PostgresRiskStore(settings.database_url))

    def execute(self, command: RiskEvaluationCommand) -> RiskEvaluationResult:
        if command.risk_input.get("risk_input_schema_version") != "woozoo.risk-input/v3":
            raise ValueError("PRODUCTION_RISK_INPUT_V3_REQUIRED")
        if command.risk_input.get("namespace") != "paper":
            raise ValueError("PRODUCTION_RISK_NAMESPACE_REQUIRED")
        if (
            command.risk_input.get("preview_policy_version")
            != "woozoo.paper-order-preview-policy/v1"
        ):
            raise ValueError("PREVIEW_POLICY_VERSION_INVALID")
        decision = evaluate_risk(command.risk_input)
        persisted = self._store.persist_decision(
            command.risk_input,
            decision,
            recorded_at=command.recorded_at,
        )
        return RiskEvaluationResult(decision=decision, persisted=persisted)

    def evaluate_proposal(self, command: RiskProposalCommand) -> RiskEvaluationResult:
        if len(command.proposal_id) != 64 or len(command.paper_account_id) != 64:
            raise ValueError("RISK_PROPOSAL_COMMAND_ID_INVALID")
        if command.recorded_at.tzinfo is None:
            raise ValueError("RECORDED_AT_MUST_BE_AWARE")
        _risk_input, decision, persisted = self._store.evaluate_proposal(
            command.proposal_id, command.paper_account_id, command.recorded_at
        )
        return RiskEvaluationResult(decision=decision, persisted=persisted)
