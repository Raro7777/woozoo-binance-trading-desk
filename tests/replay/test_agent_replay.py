from __future__ import annotations

import asyncio
import json

from agent_orchestrator import AgentWorkflow, EvidenceContext, MockLlmProvider, Role


NOW = "2026-07-20T00:00:00Z"


def test_agent_replay_is_stable_across_provider_json_key_order() -> None:
    payload = {
        "role": "MARKET_REGIME",
        "evidence_id": "evidence-replay",
        "evidence_digest": "b" * 64,
        "as_of": NOW,
        "knowledge_cutoff": NOW,
        "symbol": "ETHUSDT",
        "evidence_item_ids": ["item-replay"],
        "dependency_report_ids": [],
        "claim_times": [NOW],
        "findings": ["bounded"],
        "uncertainty": ["bounded uncertainty"],
        "invalidation_conditions": ["quality changes"],
        "confidence": "0.5",
        "stance": "NEUTRAL",
    }
    evidence = EvidenceContext(
        evidence_id="evidence-replay",
        evidence_digest="b" * 64,
        symbol="ETHUSDT",
        as_of=NOW,
        knowledge_cutoff=NOW,
        quality="healthy",
        item_ids=("item-replay",),
        quoted_content=("replay observation",),
    )
    compact = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    reversed_keys = json.dumps(dict(reversed(tuple(payload.items()))), indent=2)
    first = asyncio.run(
        AgentWorkflow(
            MockLlmProvider(overrides={Role.MARKET_REGIME: compact}), clock=lambda: NOW
        ).run(evidence)
    )
    second = asyncio.run(
        AgentWorkflow(
            MockLlmProvider(overrides={Role.MARKET_REGIME: reversed_keys}), clock=lambda: NOW
        ).run(evidence)
    )
    assert first == second
