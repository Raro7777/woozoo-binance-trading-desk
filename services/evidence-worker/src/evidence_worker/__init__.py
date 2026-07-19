"""Deterministic point-in-time market Evidence construction."""

from .builder import (
    EVIDENCE_RECIPE_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    SOURCE_REVISION,
    EvidenceBuildError,
    build_evidence_snapshot,
)
from .features import FEATURE_DEFINITION_VERSION
from .types import Candle, EvidenceSnapshot, FeatureObservation

__all__ = [
    "Candle",
    "EVIDENCE_RECIPE_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceBuildError",
    "EvidenceSnapshot",
    "FEATURE_DEFINITION_VERSION",
    "FeatureObservation",
    "SOURCE_REVISION",
    "build_evidence_snapshot",
]
