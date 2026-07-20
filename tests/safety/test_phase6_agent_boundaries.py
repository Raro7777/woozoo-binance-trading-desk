from pathlib import Path
import re
import asyncio
import json

from agent_orchestrator import AgentWorkflow, EvidenceContext, MockLlmProvider, Role


ROOT = Path(__file__).parents[2]
SERVICE = ROOT / "services/agent-orchestrator/src/agent_orchestrator"


def test_sec_002_agent_has_no_execution_or_exchange_capability() -> None:
    sources = "\n".join(path.read_text("utf-8") for path in SERVICE.rglob("*.py")).lower()
    forbidden = (
        "binance",
        "testnet",
        "api_key",
        "api_secret",
        "client_secret",
        "paperexecutionauthorization",
        "risk verdict",
        "kill recovery",
        "fastapi",
        "httpx",
        "requests",
        "websocket",
    )
    for token in forbidden:
        assert re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", sources) is None
    assert '"tool_allowlist": []' in (SERVICE / "prompts.py").read_text("utf-8")


def test_sec_002_active_api_has_no_agent_or_proposal_route() -> None:
    openapi = (ROOT / "packages/contracts/spec/openapi.v1.json").read_text("utf-8").lower()
    app = (ROOT / "services/control-api/src/control_api/app.py").read_text("utf-8").lower()
    for token in ("analysis-runs", "trade-proposals", "agent-orchestrator"):
        assert token not in openapi
        assert token not in app


def test_sec_001_has_no_real_provider_or_secret_configuration() -> None:
    files = [
        ROOT / "pyproject.toml",
        ROOT / "compose.yaml",
        ROOT / "scripts/env-init.mjs",
        *SERVICE.rglob("*.py"),
    ]
    text = "\n".join(path.read_text("utf-8") for path in files).lower()
    for token in ("openai_api_key", "anthropic_api_key", "gemini_api_key", "provider_secret"):
        assert token not in text


def test_ai_002_contract_requires_evidence_and_temporal_bounds() -> None:
    report = (ROOT / "packages/contracts/spec/agent-report.v1.json").read_text("utf-8")
    proposal = (ROOT / "packages/contracts/spec/trade-proposal.v1.json").read_text("utf-8")
    for token in (
        "evidence_id",
        "evidence_digest",
        "as_of",
        "knowledge_cutoff",
        "evidence_item_ids",
    ):
        assert token in report
        assert token in proposal


def test_ai_002_orphan_and_future_claims_hold() -> None:
    now = "2026-07-20T00:00:00Z"
    base = {
        "role": "MARKET_REGIME",
        "evidence_id": "evidence-safety",
        "evidence_digest": "b" * 64,
        "as_of": now,
        "knowledge_cutoff": now,
        "symbol": "BTCUSDT",
        "evidence_item_ids": ["member"],
        "dependency_report_ids": [],
        "claim_times": [now],
        "findings": ["bounded"],
        "uncertainty": ["unknown"],
        "invalidation_conditions": ["quality changes"],
        "confidence": "0.5",
        "stance": "NEUTRAL",
    }
    evidence = EvidenceContext(
        evidence_id="evidence-safety",
        evidence_digest="b" * 64,
        symbol="BTCUSDT",
        as_of=now,
        knowledge_cutoff=now,
        quality="healthy",
        item_ids=("member",),
    )
    for changes, expected in (
        ({"evidence_item_ids": ["orphan"]}, "ORPHAN_EVIDENCE_ITEM"),
        ({"claim_times": ["2026-07-20T00:00:01Z"]}, "TEMPORAL_BOUNDARY_VIOLATION"),
    ):
        payload = {**base, **changes}
        result = asyncio.run(
            AgentWorkflow(
                MockLlmProvider(overrides={Role.MARKET_REGIME: json.dumps(payload)}),
                clock=lambda: now,
            ).run(evidence)
        )
        assert result.run["hold_reason"] == expected
        assert result.proposal is None
