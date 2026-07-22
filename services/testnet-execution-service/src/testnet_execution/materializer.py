"""Deterministic production materialization of Testnet preview and Risk authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .canonical import CanonicalValue, canonical_digest
from .financial import TestnetRiskContext, evaluate_testnet_risk, testnet_risk_input


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    raise ValueError("TESTNET_RISK_TIMESTAMP_INVALID")


class PostgresTestnetRiskMaterializer:
    """Create one Testnet preview/Risk pair from existing deterministic authorities."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def run_once(self, *, now: datetime | None = None) -> dict[str, object] | None:
        authority_at = (now or datetime.now(UTC)).astimezone(UTC)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row = connection.execute(
                "SELECT proposal.proposal_id,proposal.proposal_hash,proposal.evidence_id,"
                "proposal.payload AS proposal_payload,paper.decision_id AS source_decision_id,"
                "paper.decision_hash AS source_decision_hash,paper.risk_input_digest AS "
                "source_input_digest,paper.risk_input AS source_input,paper.verdict AS "
                "source_verdict,paper.ordered_reason_codes AS source_reason_codes,"
                "paper.paper_order_preview_hash,paper.decision_as_of,"
                "state.*,checkpoint.ledger_snapshot_digest "
                "FROM trade_proposals proposal "
                "JOIN LATERAL (SELECT * FROM risk_decisions candidate "
                "WHERE candidate.proposal_hash=proposal.proposal_hash "
                "ORDER BY candidate.recorded_at DESC,candidate.decision_id DESC LIMIT 1) paper "
                "ON true CROSS JOIN testnet_operator_state_v1 state "
                "JOIN testnet_reconciliation_checkpoints checkpoint "
                "ON checkpoint.checkpoint_id=state.checkpoint_id "
                "WHERE proposal.risk_eligible=true AND paper.verdict='ALLOWED' "
                "AND proposal.side IN ('BUY','SELL') "
                "AND NOT EXISTS (SELECT 1 FROM testnet_risk_materialization_results result "
                "WHERE result.proposal_id=proposal.proposal_id) "
                "ORDER BY proposal.created_at,proposal.proposal_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            lock = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                (row["proposal_id"],),
            ).fetchone()
            if lock is None or lock["pg_try_advisory_xact_lock"] is not True:
                return None

            source_input = cast(dict[str, object], row["source_input"])
            source_preview = cast(dict[str, object], source_input.get("order_preview", {}))
            source_data = cast(dict[str, object], source_input.get("data", {}))
            proposal_material = cast(dict[str, object], source_input.get("proposal", {}))
            source_valid = (
                canonical_digest(cast(CanonicalValue, source_input)) == row["source_input_digest"]
                and canonical_digest(
                    cast(
                        CanonicalValue,
                        {
                            "decision_schema_version": "woozoo.risk-decision/v1",
                            "risk_input_digest": row["source_input_digest"],
                            "verdict": row["source_verdict"],
                            "ordered_reason_codes": list(
                                cast(list[object], row["source_reason_codes"] or [])
                            ),
                        },
                    )
                )
                == row["source_decision_hash"]
                and row["source_verdict"] == "ALLOWED"
                and proposal_material.get("proposal_hash") == row["proposal_hash"]
                and source_preview.get("paper_order_preview_hash")
                == row["paper_order_preview_hash"]
                and source_preview.get("symbol") in {"BTCUSDT", "ETHUSDT"}
                and source_preview.get("side") in {"BUY", "SELL"}
            )
            symbol = str(source_preview.get("symbol", ""))
            side = str(source_preview.get("side", ""))
            base_asset = symbol.removesuffix("USDT")
            balances = {
                str(item["asset"]): str(item["free"])
                for item in connection.execute(
                    "SELECT asset,free FROM testnet_asset_balances WHERE generation_id=%s",
                    (row["generation_id"],),
                ).fetchall()
            }
            knowledge_cutoff = _timestamp(source_data.get("knowledge_cutoff"))
            source_as_of = _timestamp(source_data.get("as_of"))
            data_healthy = (
                source_valid
                and source_data.get("freshness") == "FRESH"
                and source_data.get("quality") == "HEALTHY"
                and source_data.get("future_contamination") is False
                and source_data.get("watermark_complete") is True
                and knowledge_cutoff <= source_as_of <= authority_at
                and authority_at - source_as_of <= timedelta(minutes=5)
            )
            context = TestnetRiskContext(
                proposal_id=str(row["proposal_id"]),
                proposal_hash=str(row["proposal_hash"]),
                evidence_id=str(row["evidence_id"]),
                evidence_digest=str(source_data.get("evidence_hash", "")),
                data_state_digest=canonical_digest(cast(CanonicalValue, source_data)),
                account_binding_id=str(row["account_binding_id"]),
                account_generation=int(row["generation_number"]),
                symbol=cast(Literal["BTCUSDT", "ETHUSDT"], symbol),
                side=cast(Literal["BUY", "SELL"], side),
                quantity=str(source_preview.get("quantity", "")),
                limit_price=str(source_preview.get("limit_price", "")),
                available_quote=balances.get("USDT", "0"),
                available_base=balances.get(base_asset, "0"),
                symbol_rules_digest=canonical_digest(["woozoo.testnet-symbol-rules/v1", symbol]),
                ledger_snapshot_digest=str(row["ledger_snapshot_digest"]),
                reconciliation_checkpoint_digest=str(row["checkpoint_digest"]),
                paper_kill_version=int(row["paper_kill_version"]),
                testnet_barrier_version=int(row["barrier_version"]),
                as_of=authority_at,
                knowledge_cutoff=knowledge_cutoff,
                expires_at=authority_at + timedelta(minutes=5),
                data_healthy=data_healthy,
                paper_kill_active=row["paper_kill_active"] is not False,
                testnet_barrier_active=row["testnet_barrier_active"] is not False,
                reconciliation_healthy=row["reconciliation_status"] == "HEALTHY",
                ledger_healthy=row["reconciliation_status"] == "HEALTHY",
                source_risk_decision_id=str(row["source_decision_id"]),
                source_risk_decision_hash=str(row["source_decision_hash"]),
            )
            result = evaluate_testnet_risk(context)
            risk_input = testnet_risk_input(context)
            if canonical_digest(cast(CanonicalValue, risk_input)) != result.risk_input_digest:
                raise ValueError("TESTNET_RISK_INPUT_DIGEST_MISMATCH")
            if result.preview is None:
                connection.execute(
                    "INSERT INTO testnet_risk_materialization_results(proposal_id,generation_id,"
                    "risk_input_digest,verdict,ordered_reason_codes,recorded_at) "
                    "VALUES (%s,%s,%s,'ERROR',%s,%s)",
                    (
                        row["proposal_id"],
                        row["generation_id"],
                        result.risk_input_digest,
                        list(result.ordered_reason_codes),
                        authority_at,
                    ),
                )
                return {
                    "outcome": "RISK_NOT_ALLOWED",
                    "reason_codes": list(result.ordered_reason_codes),
                }
            preview = result.preview
            preview_digest = str(preview["testnet_order_preview_digest"])
            decision_id = canonical_digest(
                ["woozoo.testnet-risk-decision/v1", result.risk_input_digest, preview_digest]
            )
            decision_material: dict[str, object] = {
                "schema_version": "woozoo.testnet-risk-decision/v1",
                "decision_id": decision_id,
                "risk_input_digest": result.risk_input_digest,
                "proposal_id": row["proposal_id"],
                "preview_digest": preview_digest,
                "generation_id": row["generation_id"],
                "verdict": result.verdict,
                "policy_version": "woozoo.testnet-risk-policy/v1",
                "ordered_reason_codes": list(result.ordered_reason_codes),
                "decided_at": authority_at.isoformat().replace("+00:00", "Z"),
                "expires_at": context.expires_at.isoformat().replace("+00:00", "Z"),
            }
            decision_hash = canonical_digest(cast(CanonicalValue, decision_material))
            connection.execute(
                "INSERT INTO testnet_order_previews(preview_digest,proposal_id,proposal_hash,"
                "generation_id,symbol,side,quantity,limit_price,client_order_id,preview,"
                "created_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    preview_digest,
                    row["proposal_id"],
                    row["proposal_hash"],
                    row["generation_id"],
                    symbol,
                    side,
                    preview["quantity"],
                    preview["limit_price"],
                    preview["client_order_id"],
                    Jsonb(preview),
                    authority_at,
                    context.expires_at,
                ),
            )
            connection.execute(
                "INSERT INTO testnet_risk_decisions(decision_id,decision_hash,"
                "risk_input_digest,risk_input,proposal_id,preview_digest,generation_id,verdict,"
                "policy_version,ordered_reason_codes,decided_at,expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    decision_id,
                    decision_hash,
                    result.risk_input_digest,
                    Jsonb(risk_input),
                    row["proposal_id"],
                    preview_digest,
                    row["generation_id"],
                    result.verdict,
                    decision_material["policy_version"],
                    list(result.ordered_reason_codes),
                    authority_at,
                    context.expires_at,
                ),
            )
            connection.execute(
                "INSERT INTO testnet_risk_materialization_results(proposal_id,generation_id,"
                "risk_input_digest,decision_id,verdict,ordered_reason_codes,recorded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    row["proposal_id"],
                    row["generation_id"],
                    result.risk_input_digest,
                    decision_id,
                    result.verdict,
                    list(result.ordered_reason_codes),
                    authority_at,
                ),
            )
            return {
                "outcome": (
                    "TESTNET_RISK_MATERIALIZED"
                    if result.verdict == "ALLOWED"
                    else "RISK_NOT_ALLOWED"
                ),
                "proposal_id": row["proposal_id"],
                "preview_digest": preview_digest,
                "decision_id": decision_id,
            }
