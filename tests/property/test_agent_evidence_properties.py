from __future__ import annotations

import asyncio
import json

from agent_orchestrator import AgentWorkflow, EvidenceContext, MockLlmProvider, Role


NOW = "2026-07-20T00:00:00Z"


def test_every_orphan_citation_fails_closed() -> None:
    for ordinal in range(32):
        orphan = f"orphan-{ordinal}"
        output = {
            "role": "MARKET_REGIME",
            "evidence_id": "evidence-property",
            "evidence_digest": "c" * 64,
            "as_of": NOW,
            "knowledge_cutoff": NOW,
            "symbol": "BTCUSDT",
            "evidence_item_ids": [orphan],
            "dependency_report_ids": [],
            "claim_times": [NOW],
            "findings": ["untrusted"],
            "uncertainty": ["unknown"],
            "invalidation_conditions": ["always"],
            "confidence": "0.5",
            "stance": "NEUTRAL",
        }
        result = asyncio.run(
            AgentWorkflow(
                MockLlmProvider(overrides={Role.MARKET_REGIME: json.dumps(output)}),
                clock=lambda: NOW,
            ).run(
                EvidenceContext(
                    evidence_id="evidence-property",
                    evidence_digest="c" * 64,
                    symbol="BTCUSDT",
                    as_of=NOW,
                    knowledge_cutoff=NOW,
                    quality="healthy",
                    item_ids=("member-1", "member-2"),
                    quoted_content=("observation 1", "observation 2"),
                )
            )
        )
        assert result.run["hold_reason"] == "ORPHAN_EVIDENCE_ITEM"
        assert result.proposal is None
