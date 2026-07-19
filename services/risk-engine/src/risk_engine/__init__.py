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

__all__ = [
    "KillActivation",
    "KillActivationResult",
    "KillPersistenceStage",
    "PersistedRiskDecision",
    "PostgresKillSwitch",
    "PostgresRiskStore",
    "RiskDecision",
    "RiskPersistenceStage",
    "canonical_hash",
    "canonical_json",
    "evaluate_risk",
]
