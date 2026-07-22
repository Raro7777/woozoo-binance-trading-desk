import json
from pathlib import Path
import asyncio

from agent_orchestrator import AgentWorkflow, EvidenceContext, MockLlmProvider, Role


ROOT = Path(__file__).parents[2]


def load(name: str) -> dict[str, object]:
    return json.loads((ROOT / "packages/contracts/spec" / name).read_text("utf-8"))


def test_ai_001_contracts_are_closed_tool_free_and_dormant() -> None:
    names = [
        "prompt-manifest.v1.json",
        "agent-report.v1.json",
        "trade-proposal.v1.json",
        "analysis-run.v1.json",
        "analysis-audit.v1.json",
    ]
    contracts = [load(name) for name in names]
    assert all(contract["additionalProperties"] is False for contract in contracts)
    assert all(contract["x-creation-phase"] == 6 for contract in contracts)
    assert all(contract["x-activation-phase"] == 7 for contract in contracts)
    assert contracts[0]["properties"]["tool_allowlist"]["const"] == []  # type: ignore[index]
    proposal = contracts[2]
    assert proposal["properties"]["side"]["enum"] == ["BUY", "SELL", "HOLD"]  # type: ignore[index]
    forbidden = {"quantity", "price", "balance", "risk_verdict", "approval", "order"}
    assert forbidden.isdisjoint(proposal["properties"])  # type: ignore[arg-type]


def test_agent_events_and_risk_v2_are_test_only() -> None:
    events = load("agent-domain-events.v1.json")
    risk_v2 = load("risk-input.v2.json")
    assert events["x-activation-phase"] == 7
    assert len(events["oneOf"]) == 3  # type: ignore[arg-type]
    assert risk_v2["properties"]["namespace"]["const"] == "test"  # type: ignore[index]
    assert (
        risk_v2["properties"]["proposal"]["properties"]["payload"]["$ref"]
        == "https://schemas.woozoo.local/trade-proposal/v1"
    )  # type: ignore[index]


def test_frozen_risk_v1_fixture_contract_is_preserved() -> None:
    risk_v1 = load("risk-input.v1.json")
    proposal = risk_v1["$defs"]["proposal"]  # type: ignore[index]
    assert proposal["properties"]["fixture_contract"]["const"] == "p6-trade-proposal-consumer/v1"  # type: ignore[index]
    assert risk_v1["$id"] == "woozoo.risk-input/v1"


def test_ai_001_failure_matrix() -> None:
    now = "2026-07-20T00:00:00Z"
    evidence = EvidenceContext(
        evidence_id="evidence-contract",
        evidence_digest="a" * 64,
        symbol="BTCUSDT",
        as_of=now,
        knowledge_cutoff=now,
        quality="healthy",
        item_ids=("item-contract",),
        quoted_content=("contract observation",),
    )
    cases = (
        (MockLlmProvider(delays={Role.MARKET_REGIME: 0.02}), 0.001, "PROVIDER_TIMEOUT"),
        (MockLlmProvider(failures=frozenset({Role.TECHNICAL})), 30.0, "PROVIDER_FAILURE"),
        (MockLlmProvider(overrides={Role.MARKET_REGIME: "{"}), 30.0, "MODEL_OUTPUT_MALFORMED"),
        (MockLlmProvider(overrides={Role.MARKET_REGIME: "{}"}), 30.0, "REPORT_SCHEMA_INVALID"),
    )
    for provider, timeout, expected in cases:
        result = asyncio.run(
            AgentWorkflow(provider, timeout_seconds=timeout, clock=lambda: now).run(evidence)
        )
        assert result.run["hold_reason"] == expected
        assert result.proposal is None
