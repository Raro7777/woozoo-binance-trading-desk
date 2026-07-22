"""Closed Proposal bindings for frozen P6 Risk v2 and production Paper Risk v3."""

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


def bind_paper_risk_input(
    risk_v1: dict[str, object], proposal: dict[str, Any]
) -> dict[str, object]:
    """Bind a complete deterministic Risk v1 context to one production Proposal."""

    if risk_v1.get("risk_input_schema_version") != "woozoo.risk-input/v1":
        raise ValueError("P7_RISK_BASE_SCHEMA_INVALID")
    if risk_v1.get("namespace") != "test":
        raise ValueError("P7_RISK_BASE_NAMESPACE_INVALID")
    if proposal.get("schema_version") != "woozoo.trade-proposal/v1":
        raise ValueError("P7_PROPOSAL_SCHEMA_INVALID")
    if proposal.get("risk_eligible") is not True or proposal.get("side") not in {"BUY", "SELL"}:
        raise ValueError("P7_HOLD_NOT_RISK_ELIGIBLE")
    data = risk_v1.get("data")
    if not isinstance(data, dict):
        raise ValueError("P7_RISK_DATA_INVALID")
    if any(
        data.get(data_field) != proposal.get(proposal_field)
        for data_field, proposal_field in (
            ("evidence_id", "evidence_id"),
            ("evidence_hash", "evidence_digest"),
            ("as_of", "as_of"),
            ("knowledge_cutoff", "knowledge_cutoff"),
        )
    ):
        raise ValueError("P7_PROPOSAL_EVIDENCE_MISMATCH")
    preview = risk_v1.get("order_preview")
    if not isinstance(preview, dict) or (
        preview.get("symbol") != proposal.get("symbol")
        or preview.get("side") != proposal.get("side")
    ):
        raise ValueError("P7_PROPOSAL_PREVIEW_MISMATCH")
    bound = deepcopy(risk_v1)
    bound["risk_input_schema_version"] = "woozoo.risk-input/v3"
    bound["namespace"] = "paper"
    bound["preview_policy_version"] = "woozoo.paper-order-preview-policy/v1"
    supplied_books = risk_v1.get("market_books")
    if supplied_books is not None:
        if not isinstance(supplied_books, dict):
            raise ValueError("P7_MARKET_BOOKS_INVALID")
        market_books = deepcopy(supplied_books)
    else:
        portfolio = risk_v1.get("portfolio")
        positions = portfolio.get("positions") if isinstance(portfolio, dict) else None
        if not isinstance(positions, dict):
            raise ValueError("P7_MARKET_BOOKS_INVALID")
        market_books = {}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            position = positions.get(symbol)
            if not isinstance(position, dict) or not isinstance(position.get("midpoint"), str):
                raise ValueError("P7_MARKET_BOOKS_INVALID")
            midpoint = position["midpoint"]
            market_books[symbol] = {"best_bid": midpoint, "best_ask": midpoint}
        target_book = market_books.get(preview.get("symbol"))
        if not isinstance(target_book, dict):
            raise ValueError("P7_MARKET_BOOKS_INVALID")
        target_book["best_bid"] = preview.get("best_bid")
        target_book["best_ask"] = preview.get("best_ask")
    bound["market_books"] = market_books
    bound["proposal"] = {
        "producer_contract": "woozoo.trade-proposal/v1",
        "schema_version": "woozoo.trade-proposal/v1",
        "payload": deepcopy(proposal),
        "proposal_hash": proposal["proposal_hash"],
    }
    return bound
