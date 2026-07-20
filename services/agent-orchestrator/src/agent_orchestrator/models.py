"""Closed value objects and stable Phase 6 HOLD reasons."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    MARKET_REGIME = "MARKET_REGIME"
    TECHNICAL = "TECHNICAL"
    TRADE_FLOW = "TRADE_FLOW"
    BULL = "BULL"
    BEAR = "BEAR"
    TRADER = "TRADER"
    PORTFOLIO = "PORTFOLIO"
    AUDIT = "AUDIT"


ROLE_ORDER = tuple(Role)


class HoldReason(StrEnum):
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    EVIDENCE_DIGEST_MISMATCH = "EVIDENCE_DIGEST_MISMATCH"
    EVIDENCE_UNHEALTHY = "EVIDENCE_UNHEALTHY"
    EVIDENCE_FUTURE_CONTAMINATION = "EVIDENCE_FUTURE_CONTAMINATION"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    MODEL_OUTPUT_MALFORMED = "MODEL_OUTPUT_MALFORMED"
    REPORT_SCHEMA_INVALID = "REPORT_SCHEMA_INVALID"
    REQUIRED_REPORT_MISSING = "REQUIRED_REPORT_MISSING"
    ORPHAN_EVIDENCE_ITEM = "ORPHAN_EVIDENCE_ITEM"
    TEMPORAL_BOUNDARY_VIOLATION = "TEMPORAL_BOUNDARY_VIOLATION"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    TOOL_CALL_FORBIDDEN = "TOOL_CALL_FORBIDDEN"
    AUDIT_REJECTED = "AUDIT_REJECTED"


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    evidence_id: str
    evidence_digest: str
    symbol: str
    as_of: str
    knowledge_cutoff: str
    quality: str
    item_ids: tuple[str, ...]
    quoted_content: tuple[str, ...] = ()
    future_contamination: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    run: dict[str, Any]
    reports: tuple[dict[str, Any], ...]
    proposal: dict[str, Any] | None
    audit: dict[str, Any]
    events: tuple[dict[str, Any], ...]
