from __future__ import annotations

import asyncio

from agent_orchestrator import AgentWorkflow, EvidenceContext, MockLlmProvider, Role


NOW = "2026-07-20T00:00:00Z"


def test_provider_failure_at_every_role_has_no_proposal() -> None:
    for role in Role:
        result = asyncio.run(
            AgentWorkflow(MockLlmProvider(failures=frozenset({role})), clock=lambda: NOW).run(
                EvidenceContext(
                    evidence_id="evidence-failure",
                    evidence_digest="d" * 64,
                    symbol="BTCUSDT",
                    as_of=NOW,
                    knowledge_cutoff=NOW,
                    quality="healthy",
                    item_ids=("item-failure",),
                )
            )
        )
        assert result.run["outcome"] == "HOLD"
        assert result.run["hold_reason"] == "PROVIDER_FAILURE"
        assert result.proposal is None
        assert all(event["event_type"] != "trade.proposal.created.v1" for event in result.events)
