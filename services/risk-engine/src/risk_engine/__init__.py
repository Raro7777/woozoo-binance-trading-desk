"""Phase 5 deterministic, IO-free Risk Engine authority."""

from platform_core import canonical_hash, canonical_json

from .engine import evaluate_risk
from .kill_switch import (
    KillActivation,
    KillActivationResult,
    KillPersistenceStage,
    PostgresKillSwitch,
)
from .models import RiskDecision
from .persistence import PersistedRiskDecision, PostgresRiskStore, RiskPersistenceStage
from .reconciliation_kill import PostgresReconciliationKillHandler, ReconciliationKillResult
from .service import (
    RiskDecisionService,
    RiskEvaluationCommand,
    RiskEvaluationResult,
    RiskProposalCommand,
)

__all__ = [
    "KillActivation",
    "KillActivationResult",
    "KillPersistenceStage",
    "PersistedRiskDecision",
    "PostgresKillSwitch",
    "PostgresRiskStore",
    "PostgresReconciliationKillHandler",
    "RiskDecision",
    "RiskDecisionService",
    "RiskEvaluationCommand",
    "RiskEvaluationResult",
    "RiskProposalCommand",
    "RiskPersistenceStage",
    "ReconciliationKillResult",
    "canonical_hash",
    "canonical_json",
    "evaluate_risk",
]
