"""Narrow production command port for deterministic, tool-free Paper analysis."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import EvidenceContext, WorkflowResult
from .persistence import AnalysisCommandReceipt, PersistedAnalysis, PostgresAgentStore
from .provider import MockLlmProvider
from .settings import AgentSettings
from .workflow import AgentWorkflow


class AgentStore(Protocol):
    def load_evidence(self, evidence_id: str) -> EvidenceContext | None: ...

    def latest_healthy_evidence_id(self, symbol: str, observed_at: datetime) -> str | None: ...

    def persist(
        self,
        result: WorkflowResult,
        *,
        command_receipt: AnalysisCommandReceipt | None = None,
    ) -> PersistedAnalysis: ...


@dataclass(frozen=True, slots=True)
class AnalysisCommand:
    evidence_id: str
    idempotency_key: str | None = None
    request_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisCommandResult:
    status: str
    hold_reason: str | None
    workflow: WorkflowResult
    persisted: PersistedAnalysis | None


class AgentAnalysisService:
    """Load one immutable Evidence identity and create at most one Proposal graph."""

    def __init__(self, store: AgentStore, *, clock: Callable[[], str]) -> None:
        self._store = store
        self._clock = clock

    @classmethod
    def from_env(cls, *, clock: Callable[[], str]) -> "AgentAnalysisService":
        settings = AgentSettings.from_env()
        return cls(PostgresAgentStore(settings.database_url), clock=clock)

    def execute(self, command: AnalysisCommand) -> AnalysisCommandResult:
        if len(command.evidence_id) != 64:
            raise ValueError("EVIDENCE_ID_INVALID")
        evidence = self._store.load_evidence(command.evidence_id)
        if evidence is None:
            raise ValueError("EVIDENCE_NOT_FOUND")
        if (command.idempotency_key is None) != (command.request_hash is None):
            raise ValueError("ANALYSIS_RECEIPT_BINDING_INCOMPLETE")
        receipt = (
            None
            if command.idempotency_key is None
            else AnalysisCommandReceipt(
                idempotency_key=command.idempotency_key,
                request_hash=command.request_hash or "",
                evidence_id=command.evidence_id,
            )
        )
        workflow = asyncio.run(
            AgentWorkflow(MockLlmProvider(), clock=self._clock, namespace="paper").run(evidence)
        )
        persisted = (
            self._store.persist(workflow)
            if receipt is None
            else self._store.persist(workflow, command_receipt=receipt)
        )
        return AnalysisCommandResult(
            status=persisted.outcome,
            hold_reason=persisted.hold_reason,
            workflow=workflow,
            persisted=persisted,
        )

    def latest_healthy_evidence_id(self, symbol: str, observed_at: datetime) -> str | None:
        """Resolve an immutable Evidence identity without exposing market rows to Control."""

        return self._store.latest_healthy_evidence_id(symbol, observed_at)
