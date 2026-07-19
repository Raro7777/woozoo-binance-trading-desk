"""Closed Phase 5 RiskDecision value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_schema_version: str
    risk_input_digest: str
    verdict: str
    ordered_reason_codes: tuple[str, ...]
    primary_reason_code: str
    decision_hash: str

