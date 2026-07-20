from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from agent_orchestrator import (
    AgentWorkflow,
    EvidenceContext,
    MockLlmProvider,
    bind_test_risk_input,
)
from risk_engine import evaluate_risk
from test_risk_engine import risk_input


NOW = "2026-07-19T00:00:00Z"


def test_p6_authoritative_proposal_binds_full_hash_to_test_risk_v2() -> None:
    result = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(
            EvidenceContext(
                evidence_id="evidence-1",
                evidence_digest="a" * 64,
                symbol="BTCUSDT",
                as_of=NOW,
                knowledge_cutoff=NOW,
                quality="healthy",
                item_ids=("item-1",),
                quoted_content=("public market observation",),
            )
        )
    )
    assert result.proposal is not None
    bound = bind_test_risk_input(risk_input(), result.proposal)
    decision = evaluate_risk(bound)
    assert decision.verdict == "ALLOWED"
    assert decision.ordered_reason_codes == ("RISK_ALLOWED",)


def test_p6_hold_is_not_risk_eligible() -> None:
    result = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(
            EvidenceContext(
                evidence_id="evidence-1",
                evidence_digest="a" * 64,
                symbol="BTCUSDT",
                as_of=NOW,
                knowledge_cutoff=NOW,
                quality="stale",
                item_ids=("item-1",),
                quoted_content=("public market observation",),
            )
        )
    )
    assert result.proposal is None
    with pytest.raises(ValueError, match="P6_PROPOSAL_SCHEMA_INVALID"):
        bind_test_risk_input(risk_input(), {})


def test_p6_risk_rejects_proposal_data_evidence_mismatch() -> None:
    result = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(
            EvidenceContext(
                evidence_id="evidence-1",
                evidence_digest="a" * 64,
                symbol="BTCUSDT",
                as_of=NOW,
                knowledge_cutoff=NOW,
                quality="healthy",
                item_ids=("item-1",),
                quoted_content=("public market observation",),
            )
        )
    )
    assert result.proposal is not None
    bound = bind_test_risk_input(risk_input(), result.proposal)
    for field, value in (
        ("evidence_id", "different-evidence"),
        ("evidence_hash", "b" * 64),
        ("as_of", "2026-07-18T23:59:59Z"),
        ("knowledge_cutoff", "2026-07-18T23:59:59Z"),
    ):
        mismatched = deepcopy(bound)
        data = mismatched["data"]
        assert isinstance(data, dict)
        data[field] = value
        decision = evaluate_risk(mismatched)
        assert decision.verdict == "DENIED"
        assert "PROPOSAL_HASH_MISMATCH" in decision.ordered_reason_codes
