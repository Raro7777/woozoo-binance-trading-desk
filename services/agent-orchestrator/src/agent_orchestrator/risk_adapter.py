"""Test-namespace-only P6 Proposal binding for dormant Risk v2 verification."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def bind_test_risk_input(risk_v1: dict[str, object], proposal: dict[str, Any]) -> dict[str, object]:
    if risk_v1.get("namespace") != "test":
        raise ValueError("P6_RISK_CHAIN_TEST_NAMESPACE_ONLY")
    if proposal.get("schema_version") != "woozoo.trade-proposal/v1":
        raise ValueError("P6_PROPOSAL_SCHEMA_INVALID")
    if proposal.get("risk_eligible") is not True or proposal.get("side") not in {"BUY", "SELL"}:
        raise ValueError("P6_HOLD_NOT_RISK_ELIGIBLE")
    data = risk_v1.get("data")
    if not isinstance(data, dict):
        raise ValueError("P6_RISK_DATA_INVALID")
    if data.get("evidence_id") != proposal.get("evidence_id") or data.get(
        "evidence_hash"
    ) != proposal.get("evidence_digest"):
        raise ValueError("P6_PROPOSAL_EVIDENCE_MISMATCH")
    preview = risk_v1.get("order_preview")
    if not isinstance(preview, dict) or (
        preview.get("symbol") != proposal.get("symbol")
        or preview.get("side") != proposal.get("side")
    ):
        raise ValueError("P6_PROPOSAL_PREVIEW_MISMATCH")
    bound = deepcopy(risk_v1)
    bound["risk_input_schema_version"] = "woozoo.risk-input/v2"
    bound["proposal"] = {
        "producer_contract": "woozoo.trade-proposal/v1",
        "schema_version": "woozoo.trade-proposal/v1",
        "payload": deepcopy(proposal),
        "proposal_hash": proposal["proposal_hash"],
    }
    return bound
