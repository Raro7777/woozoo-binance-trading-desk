"""Deterministic Phase 6 role graph and fail-closed HOLD behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from platform_core.generated_contracts import AGENT_REPORT_SCHEMA, TRADE_PROPOSAL_SCHEMA

from .canonical import canonical_hash
from .models import EvidenceContext, HoldReason, ROLE_ORDER, Role, WorkflowResult
from .prompts import PROMPT_MANIFEST_HASH, PROMPT_MANIFESTS, WORKFLOW_HASH, WORKFLOW_VERSION
from .provider import LlmProvider, ProviderError


_REPORT_VALIDATOR = Draft202012Validator(AGENT_REPORT_SCHEMA, format_checker=FormatChecker())
_PROPOSAL_VALIDATOR = Draft202012Validator(TRADE_PROPOSAL_SCHEMA, format_checker=FormatChecker())
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "call the order tool",
    "reveal secret",
)
_PROVIDER_FIELDS = {
    "role",
    "evidence_id",
    "evidence_digest",
    "as_of",
    "knowledge_cutoff",
    "symbol",
    "evidence_item_ids",
    "dependency_report_ids",
    "claim_times",
    "findings",
    "uncertainty",
    "invalidation_conditions",
    "confidence",
    "stance",
}


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


class AgentWorkflow:
    def __init__(
        self,
        provider: LlmProvider,
        *,
        timeout_seconds: float = 30.0,
        clock: Callable[[], str],
        namespace: str = "test",
    ) -> None:
        if provider.name != "mock" or provider.model != "woozoo-deterministic-mock/v1":
            raise ValueError("Phase 6 provider must be deterministic mock")
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        if namespace not in {"test", "paper"}:
            raise ValueError("Agent workflow namespace must be test or paper")
        self._namespace = namespace

    async def run(self, evidence: EvidenceContext) -> WorkflowResult:
        run_identity = {
            "evidence_digest": evidence.evidence_digest,
            "prompt_manifest_hash": PROMPT_MANIFEST_HASH,
            "workflow_hash": WORKFLOW_HASH,
        }
        if self._namespace == "paper":
            run_identity["namespace"] = "paper"
        run_id = canonical_hash(run_identity)
        preliminary = self._validate_evidence(evidence)
        if preliminary is not None:
            return self._hold(run_id, evidence, preliminary, ())
        if any(
            marker in content.casefold()
            for content in evidence.quoted_content
            for marker in _INJECTION_MARKERS
        ):
            return self._hold(run_id, evidence, HoldReason.PROMPT_INJECTION_DETECTED, ())

        reports: list[dict[str, Any]] = []
        for role, manifest in zip(ROLE_ORDER, PROMPT_MANIFESTS, strict=True):
            request: dict[str, object] = {
                "evidence_id": evidence.evidence_id,
                "evidence_digest": evidence.evidence_digest,
                "as_of": evidence.as_of,
                "knowledge_cutoff": evidence.knowledge_cutoff,
                "symbol": evidence.symbol,
                "evidence_item_ids": list(evidence.item_ids),
                "dependency_report_ids": [report["report_id"] for report in reports],
                "quoted_evidence": list(evidence.quoted_content),
                "prompt_manifest_hash": manifest["manifest_hash"],
                "workflow_hash": WORKFLOW_HASH,
                "tool_allowlist": [],
            }
            try:
                raw = await asyncio.wait_for(
                    self._provider.invoke(role, request), timeout=self._timeout_seconds
                )
            except TimeoutError:
                return self._hold(run_id, evidence, HoldReason.PROVIDER_TIMEOUT, tuple(reports))
            except ProviderError:
                return self._hold(run_id, evidence, HoldReason.PROVIDER_FAILURE, tuple(reports))
            try:
                output = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return self._hold(
                    run_id, evidence, HoldReason.MODEL_OUTPUT_MALFORMED, tuple(reports)
                )
            if not isinstance(output, dict):
                return self._hold(
                    run_id, evidence, HoldReason.MODEL_OUTPUT_MALFORMED, tuple(reports)
                )
            output = cast(dict[str, Any], output)
            if "tool_calls" in output:
                return self._hold(run_id, evidence, HoldReason.TOOL_CALL_FORBIDDEN, tuple(reports))
            if set(output) != _PROVIDER_FIELDS:
                return self._hold(
                    run_id, evidence, HoldReason.REPORT_SCHEMA_INVALID, tuple(reports)
                )
            validation_reason = self._validate_provider_output(
                role, output, evidence, tuple(reports)
            )
            if validation_reason is not None:
                return self._hold(run_id, evidence, validation_reason, tuple(reports))
            report = self._report(run_id, role, output, cast(str, manifest["manifest_hash"]))
            try:
                _REPORT_VALIDATOR.validate(report)
            except Exception:
                return self._hold(
                    run_id, evidence, HoldReason.REPORT_SCHEMA_INVALID, tuple(reports)
                )
            reports.append(report)

        if tuple(report["role"] for report in reports) != tuple(role.value for role in ROLE_ORDER):
            return self._hold(run_id, evidence, HoldReason.REQUIRED_REPORT_MISSING, tuple(reports))
        proposal = self._proposal(run_id, evidence, tuple(reports))
        try:
            _PROPOSAL_VALIDATOR.validate(proposal)
        except Exception:
            return self._hold(run_id, evidence, HoldReason.AUDIT_REJECTED, tuple(reports))
        audit = self._audit(run_id, evidence, tuple(reports), proposal, ())
        run = self._run_record(run_id, evidence, tuple(reports), proposal, audit, None)
        events = (
            self._event("analysis.run.completed.v1", run_id, run),
            self._event("trade.proposal.created.v1", proposal["proposal_id"], proposal),
        )
        return WorkflowResult(run, tuple(reports), proposal, audit, events)

    def _validate_evidence(self, evidence: EvidenceContext) -> HoldReason | None:
        if not evidence.evidence_id or not evidence.item_ids:
            return HoldReason.EVIDENCE_NOT_FOUND
        if len(evidence.quoted_content) != len(evidence.item_ids) or any(
            not content.strip() for content in evidence.quoted_content
        ):
            return HoldReason.EVIDENCE_NOT_FOUND
        if len(evidence.evidence_digest) != 64:
            return HoldReason.EVIDENCE_DIGEST_MISMATCH
        if evidence.quality != "healthy":
            return HoldReason.EVIDENCE_UNHEALTHY
        if evidence.future_contamination:
            return HoldReason.EVIDENCE_FUTURE_CONTAMINATION
        try:
            if _utc(evidence.as_of) > _utc(evidence.knowledge_cutoff):
                return HoldReason.TEMPORAL_BOUNDARY_VIOLATION
        except ValueError:
            return HoldReason.TEMPORAL_BOUNDARY_VIOLATION
        return None

    def _validate_provider_output(
        self,
        role: Role,
        output: Mapping[str, Any],
        evidence: EvidenceContext,
        reports: tuple[dict[str, Any], ...],
    ) -> HoldReason | None:
        if output["role"] != role.value:
            return HoldReason.REQUIRED_REPORT_MISSING
        for field in ("evidence_id", "evidence_digest", "as_of", "knowledge_cutoff", "symbol"):
            if output[field] != getattr(evidence, field):
                return HoldReason.EVIDENCE_DIGEST_MISMATCH
        citations = output["evidence_item_ids"]
        if (
            not isinstance(citations, list)
            or not citations
            or not set(citations) <= set(evidence.item_ids)
        ):
            return HoldReason.ORPHAN_EVIDENCE_ITEM
        expected_dependencies = [report["report_id"] for report in reports]
        if output["dependency_report_ids"] != expected_dependencies:
            return HoldReason.REQUIRED_REPORT_MISSING
        claim_times = output["claim_times"]
        if not isinstance(claim_times, list) or not claim_times:
            return HoldReason.REPORT_SCHEMA_INVALID
        try:
            boundary = min(_utc(evidence.as_of), _utc(evidence.knowledge_cutoff))
            if any(not isinstance(value, str) or _utc(value) > boundary for value in claim_times):
                return HoldReason.TEMPORAL_BOUNDARY_VIOLATION
        except ValueError:
            return HoldReason.TEMPORAL_BOUNDARY_VIOLATION
        return None

    def _report(
        self, run_id: str, role: Role, output: Mapping[str, Any], prompt_hash: str
    ) -> dict[str, Any]:
        unsigned = {
            "schema_version": "woozoo.agent-report/v1",
            "run_id": run_id,
            "role": role.value,
            "report_version": "v1",
            **output,
            "provider": self._provider.name,
            "model": self._provider.model,
            "prompt_manifest_hash": prompt_hash,
            "workflow_hash": WORKFLOW_HASH,
        }
        report_hash = canonical_hash(cast(dict[str, object], unsigned))
        return {
            **unsigned,
            "report_id": canonical_hash(
                {"kind": "agent-report", "report_hash": report_hash, "run_id": run_id}
            ),
            "report_hash": report_hash,
        }

    def _proposal(
        self,
        run_id: str,
        evidence: EvidenceContext,
        reports: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        trader = next(report for report in reports if report["role"] == Role.TRADER.value)
        portfolio = next(report for report in reports if report["role"] == Role.PORTFOLIO.value)
        side = trader["stance"]
        if portfolio["stance"] == "HOLD" or side not in {"BUY", "SELL"}:
            side = "HOLD"
        unsigned: dict[str, Any] = {
            "schema_version": "woozoo.trade-proposal/v1",
            "proposal_version": "v1",
            "analysis_run_id": run_id,
            "evidence_id": evidence.evidence_id,
            "evidence_digest": evidence.evidence_digest,
            "as_of": evidence.as_of,
            "knowledge_cutoff": evidence.knowledge_cutoff,
            "symbol": evidence.symbol,
            "side": side,
            "risk_eligible": side in {"BUY", "SELL"},
            "report_ids": [report["report_id"] for report in reports],
            "report_hashes": [report["report_hash"] for report in reports],
            "evidence_item_ids": sorted(
                {item for report in reports for item in report["evidence_item_ids"]}
            ),
            "thesis": trader["findings"][0],
            "uncertainty": trader["uncertainty"],
            "invalidation_conditions": trader["invalidation_conditions"],
            "confidence": trader["confidence"],
            "provider": self._provider.name,
            "model": self._provider.model,
            "prompt_manifest_hash": PROMPT_MANIFEST_HASH,
            "workflow_version": WORKFLOW_VERSION,
            "workflow_hash": WORKFLOW_HASH,
        }
        proposal_hash = canonical_hash(cast(dict[str, object], unsigned))
        return {
            **unsigned,
            "proposal_id": canonical_hash(
                {"kind": "trade-proposal", "proposal_hash": proposal_hash, "run_id": run_id}
            ),
            "proposal_hash": proposal_hash,
        }

    def _audit(
        self,
        run_id: str,
        evidence: EvidenceContext,
        reports: tuple[dict[str, Any], ...],
        proposal: dict[str, Any] | None,
        reasons: tuple[HoldReason, ...],
    ) -> dict[str, Any]:
        unsigned: dict[str, Any] = {
            "schema_version": "woozoo.analysis-audit/v1",
            "run_id": run_id,
            "verdict": "HOLD" if reasons else "ACCEPTED",
            "reason_codes": [reason.value for reason in reasons],
            "evidence_digest": evidence.evidence_digest,
            "report_hashes": [report["report_hash"] for report in reports],
            "proposal_hash": None if proposal is None else proposal["proposal_hash"],
            "workflow_hash": WORKFLOW_HASH,
            "prompt_manifest_hash": PROMPT_MANIFEST_HASH,
            "audited_at": self._clock(),
        }
        audit_hash = canonical_hash(cast(dict[str, object], unsigned))
        return {
            **unsigned,
            "audit_id": canonical_hash({"kind": "analysis-audit", "audit_hash": audit_hash}),
            "audit_hash": audit_hash,
        }

    def _run_record(
        self,
        run_id: str,
        evidence: EvidenceContext,
        reports: tuple[dict[str, Any], ...],
        proposal: dict[str, Any] | None,
        audit: dict[str, Any],
        reason: HoldReason | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": (
                "woozoo.analysis-run/v2" if self._namespace == "paper" else "woozoo.analysis-run/v1"
            ),
            "run_id": run_id,
            "namespace": self._namespace,
            "evidence_id": evidence.evidence_id,
            "evidence_digest": evidence.evidence_digest,
            "symbol": evidence.symbol,
            "as_of": evidence.as_of,
            "knowledge_cutoff": evidence.knowledge_cutoff,
            "workflow_version": WORKFLOW_VERSION,
            "workflow_hash": WORKFLOW_HASH,
            "prompt_manifest_hash": PROMPT_MANIFEST_HASH,
            "provider": self._provider.name,
            "model": self._provider.model,
            **({"tool_count": 0} if self._namespace == "paper" else {}),
            "outcome": "HOLD" if reason else "COMPLETED",
            "hold_reason": None if reason is None else reason.value,
            "report_ids": [report["report_id"] for report in reports],
            "proposal_id": None if proposal is None else proposal["proposal_id"],
            **({"risk_decision_id": None} if self._namespace == "paper" else {}),
            "audit_hash": audit["audit_hash"],
        }

    def _hold(
        self,
        run_id: str,
        evidence: EvidenceContext,
        reason: HoldReason,
        reports: tuple[dict[str, Any], ...],
    ) -> WorkflowResult:
        audit = self._audit(run_id, evidence, reports, None, (reason,))
        run = self._run_record(run_id, evidence, reports, None, audit, reason)
        event = self._event("analysis.run.held.v1", run_id, run)
        return WorkflowResult(run, reports, None, audit, (event,))

    def _event(
        self, event_type: str, aggregate_id: object, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload_hash = canonical_hash(cast(dict[str, object], dict(data)))
        unsigned: dict[str, Any] = {
            "spec_version": "woozoo.event/v1",
            "event_type": event_type,
            "event_version": 1,
            "occurred_at": self._clock(),
            "producer": "agent-orchestrator",
            "activation_phase": 7,
            "aggregate_id": aggregate_id,
            "aggregate_version": 1,
            "payload_hash": payload_hash,
            "data": dict(data),
        }
        return {**unsigned, "event_id": canonical_hash(cast(dict[str, object], unsigned))}
