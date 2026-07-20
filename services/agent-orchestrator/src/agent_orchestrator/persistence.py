"""Atomic append-only persistence for Phase 6 analysis results and outbox events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

import psycopg
from psycopg.types.json import Jsonb
from jsonschema import Draft202012Validator, FormatChecker

from platform_core.generated_contracts import (
    AGENT_REPORT_SCHEMA,
    ANALYSIS_AUDIT_SCHEMA,
    ANALYSIS_RUN_SCHEMA,
    TRADE_PROPOSAL_SCHEMA,
)

from .canonical import canonical_hash
from .models import EvidenceContext, WorkflowResult


_REPORT_VALIDATOR = Draft202012Validator(AGENT_REPORT_SCHEMA, format_checker=FormatChecker())
_PROPOSAL_VALIDATOR = Draft202012Validator(TRADE_PROPOSAL_SCHEMA, format_checker=FormatChecker())
_RUN_VALIDATOR = Draft202012Validator(ANALYSIS_RUN_SCHEMA, format_checker=FormatChecker())
_AUDIT_VALIDATOR = Draft202012Validator(ANALYSIS_AUDIT_SCHEMA, format_checker=FormatChecker())


class AgentPersistenceStage(StrEnum):
    RUN = "run"
    REPORTS = "reports"
    PROPOSAL = "proposal"
    AUDIT = "audit"
    OUTBOX = "outbox"


@dataclass(frozen=True, slots=True)
class PersistedAnalysis:
    created: bool
    run_id: str
    proposal_id: str | None
    audit_hash: str


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("AGENT_TIMESTAMP_INVALID")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("AGENT_TIMESTAMP_INVALID")
    return parsed


def _without(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in keys}


def _require_hash(record: dict[str, Any], hash_field: str, *excluded: str) -> None:
    if canonical_hash(_without(record, hash_field, *excluded)) != record.get(hash_field):
        raise ValueError(f"{hash_field.upper()}_MISMATCH")


class PostgresAgentStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _fail(stage: AgentPersistenceStage, requested: AgentPersistenceStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_AGENT_FAILURE:{stage.value}")

    def load_evidence(self, evidence_id: str) -> EvidenceContext | None:
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                """
                SELECT reader.evidence_id,reader.evidence_digest,reader.symbol,
                       reader.as_of,reader.knowledge_cutoff,
                       reader.quality_status,reader.item_id,reader.item_type,
                       CASE
                         WHEN item_type='normalized_market_event' THEN
                           jsonb_build_object(
                             'item_id',reader.item_id,'item_type',reader.item_type,
                             'event_time',candle.event_time,'received_at',candle.received_at,
                             'payload',candle.payload)::text
                         WHEN item_type='feature_observation' THEN
                           jsonb_build_object(
                             'item_id',reader.item_id,'item_type',reader.item_type,
                             'feature_name',feature.feature_name,
                             'feature_version',feature.feature_version,
                             'interval',feature.interval,'as_of',feature.as_of,
                             'value_text',feature.value_text,
                             'input_digest',feature.input_digest)::text
                       END AS quoted_content
                FROM evidence_reader_v1 reader
                LEFT JOIN evidence_candle_reader_v1 candle
                  ON candle.evidence_id=reader.evidence_id
                 AND candle.normalized_event_id=reader.item_id
                LEFT JOIN evidence_feature_reader_v1 feature
                  ON feature.evidence_id=reader.evidence_id
                 AND feature.feature_id=reader.item_id
                WHERE reader.evidence_id=%s ORDER BY item_ordinal
                """,
                (evidence_id,),
            ).fetchall()
        if not rows or any(row[8] is None for row in rows):
            return None
        first = rows[0]
        return EvidenceContext(
            evidence_id=first[0],
            evidence_digest=first[1],
            symbol=first[2],
            as_of=first[3].isoformat().replace("+00:00", "Z"),
            knowledge_cutoff=first[4].isoformat().replace("+00:00", "Z"),
            quality=first[5],
            item_ids=tuple(row[6] for row in rows),
            quoted_content=tuple(row[8] for row in rows),
        )

    @staticmethod
    def _validate_graph(result: WorkflowResult) -> None:
        run = result.run
        _RUN_VALIDATOR.validate(run)
        _AUDIT_VALIDATOR.validate(result.audit)
        run_id = cast(str, run["run_id"])
        expected_run_id = canonical_hash(
            {
                "evidence_digest": run["evidence_digest"],
                "prompt_manifest_hash": run["prompt_manifest_hash"],
                "workflow_hash": run["workflow_hash"],
            }
        )
        if run_id != expected_run_id:
            raise ValueError("RUN_ID_MISMATCH")
        expected_report_ids: list[str] = []
        expected_report_hashes: list[str] = []
        evidence_fields = (
            "evidence_id",
            "evidence_digest",
            "symbol",
            "as_of",
            "knowledge_cutoff",
        )
        for report in result.reports:
            _REPORT_VALIDATOR.validate(report)
            _require_hash(report, "report_hash", "report_id")
            expected_report_id = canonical_hash(
                {
                    "kind": "agent-report",
                    "report_hash": report["report_hash"],
                    "run_id": run_id,
                }
            )
            if report["report_id"] != expected_report_id or report["run_id"] != run_id:
                raise ValueError("REPORT_ID_OR_RUN_MISMATCH")
            if any(report[field] != run[field] for field in evidence_fields):
                raise ValueError("REPORT_EVIDENCE_MISMATCH")
            if report["dependency_report_ids"] != expected_report_ids:
                raise ValueError("REPORT_DEPENDENCY_MISMATCH")
            expected_report_ids.append(cast(str, report["report_id"]))
            expected_report_hashes.append(cast(str, report["report_hash"]))
        if run["report_ids"] != expected_report_ids:
            raise ValueError("RUN_REPORT_MISMATCH")
        proposal = result.proposal
        if proposal is None:
            if run["proposal_id"] is not None:
                raise ValueError("RUN_PROPOSAL_MISMATCH")
        else:
            _PROPOSAL_VALIDATOR.validate(proposal)
            _require_hash(proposal, "proposal_hash", "proposal_id")
            expected_proposal_id = canonical_hash(
                {
                    "kind": "trade-proposal",
                    "proposal_hash": proposal["proposal_hash"],
                    "run_id": run_id,
                }
            )
            if (
                proposal["proposal_id"] != expected_proposal_id
                or proposal["analysis_run_id"] != run_id
                or run["proposal_id"] != proposal["proposal_id"]
                or proposal["report_ids"] != expected_report_ids
                or proposal["report_hashes"] != expected_report_hashes
                or any(proposal[field] != run[field] for field in evidence_fields)
            ):
                raise ValueError("PROPOSAL_GRAPH_MISMATCH")
        audit = result.audit
        _require_hash(audit, "audit_hash", "audit_id")
        expected_audit_id = canonical_hash(
            {"kind": "analysis-audit", "audit_hash": audit["audit_hash"]}
        )
        expected_proposal_hash = None if proposal is None else proposal["proposal_hash"]
        if (
            audit["audit_id"] != expected_audit_id
            or audit["run_id"] != run_id
            or audit["evidence_digest"] != run["evidence_digest"]
            or audit["report_hashes"] != expected_report_hashes
            or audit["proposal_hash"] != expected_proposal_hash
            or run["audit_hash"] != audit["audit_hash"]
        ):
            raise ValueError("AUDIT_GRAPH_MISMATCH")
        expected_event_types = (
            ("analysis.run.held.v1",)
            if proposal is None
            else ("analysis.run.completed.v1", "trade.proposal.created.v1")
        )
        if tuple(event.get("event_type") for event in result.events) != expected_event_types:
            raise ValueError("EVENT_SET_MISMATCH")
        for event in result.events:
            data = event.get("data")
            if not isinstance(data, dict) or canonical_hash(data) != event.get("payload_hash"):
                raise ValueError("EVENT_PAYLOAD_HASH_MISMATCH")
            expected_data = proposal if event["event_type"] == "trade.proposal.created.v1" else run
            expected_aggregate = (
                proposal["proposal_id"]
                if event["event_type"] == "trade.proposal.created.v1" and proposal is not None
                else run_id
            )
            unsigned = _without(event, "event_id")
            if (
                data != expected_data
                or event.get("aggregate_id") != expected_aggregate
                or canonical_hash(unsigned) != event.get("event_id")
            ):
                raise ValueError("EVENT_GRAPH_MISMATCH")

    def persist(
        self,
        result: WorkflowResult,
        *,
        _fail_after: AgentPersistenceStage | None = None,
    ) -> PersistedAnalysis:
        run = result.run
        run_id = cast(str, run["run_id"])
        self._validate_graph(result)
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                evidence_rows = connection.execute(
                    """
                    SELECT evidence_digest,symbol,as_of,knowledge_cutoff,quality_status,item_id
                    FROM evidence_reader_v1 WHERE evidence_id=%s ORDER BY item_ordinal
                    """,
                    (run["evidence_id"],),
                ).fetchall()
                if not evidence_rows:
                    raise ValueError("AUTHORITATIVE_EVIDENCE_NOT_FOUND")
                first = evidence_rows[0]
                authoritative = (
                    first[0],
                    first[1],
                    first[2],
                    first[3],
                )
                supplied = (
                    run["evidence_digest"],
                    run["symbol"],
                    _timestamp(run["as_of"]),
                    _timestamp(run["knowledge_cutoff"]),
                )
                if authoritative != supplied or first[4] != "healthy":
                    raise ValueError("AUTHORITATIVE_EVIDENCE_MISMATCH")
                member_ids = {row[5] for row in evidence_rows}
                cited_ids = {
                    item_id for report in result.reports for item_id in report["evidence_item_ids"]
                }
                if result.proposal is not None:
                    cited_ids.update(result.proposal["evidence_item_ids"])
                if not cited_ids <= member_ids:
                    raise ValueError("AUTHORITATIVE_EVIDENCE_CITATION_MISMATCH")
                existing = connection.execute(
                    "SELECT payload FROM analysis_runs WHERE run_id=%s", (run_id,)
                ).fetchone()
                if existing is not None:
                    if existing[0] != run:
                        raise ValueError("ANALYSIS_RUN_IDEMPOTENCY_CONFLICT")
                    return PersistedAnalysis(
                        created=False,
                        run_id=run_id,
                        proposal_id=cast(str | None, run["proposal_id"]),
                        audit_hash=cast(str, run["audit_hash"]),
                    )
                created_at = _timestamp(result.audit["audited_at"])
                connection.execute(
                    """
                    INSERT INTO analysis_runs(
                      run_id,namespace,evidence_id,evidence_digest,symbol,as_of,
                      knowledge_cutoff,workflow_version,workflow_hash,prompt_manifest_hash,
                      provider,model,outcome,hold_reason,payload,created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        run["namespace"],
                        run["evidence_id"],
                        run["evidence_digest"],
                        run["symbol"],
                        _timestamp(run["as_of"]),
                        _timestamp(run["knowledge_cutoff"]),
                        run["workflow_version"],
                        run["workflow_hash"],
                        run["prompt_manifest_hash"],
                        run["provider"],
                        run["model"],
                        run["outcome"],
                        run["hold_reason"],
                        Jsonb(run),
                        created_at,
                    ),
                )
                self._fail(AgentPersistenceStage.RUN, _fail_after)
                for report in result.reports:
                    connection.execute(
                        """
                        INSERT INTO agent_reports(
                          report_id,run_id,evidence_id,role,report_version,report_hash,payload,created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            report["report_id"],
                            run_id,
                            report["evidence_id"],
                            report["role"],
                            report["report_version"],
                            report["report_hash"],
                            Jsonb(report),
                            created_at,
                        ),
                    )
                    for item_id in report["evidence_item_ids"]:
                        connection.execute(
                            "INSERT INTO agent_report_evidence_refs(report_id,evidence_id,item_id) VALUES (%s,%s,%s)",
                            (report["report_id"], report["evidence_id"], item_id),
                        )
                self._fail(AgentPersistenceStage.REPORTS, _fail_after)
                proposal_id: str | None = None
                if result.proposal is not None:
                    proposal = result.proposal
                    proposal_id = cast(str, proposal["proposal_id"])
                    connection.execute(
                        """
                        INSERT INTO trade_proposals(
                          proposal_id,run_id,evidence_id,proposal_version,side,risk_eligible,
                          proposal_hash,payload,created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            proposal_id,
                            run_id,
                            proposal["evidence_id"],
                            proposal["proposal_version"],
                            proposal["side"],
                            proposal["risk_eligible"],
                            proposal["proposal_hash"],
                            Jsonb(proposal),
                            created_at,
                        ),
                    )
                    for item_id in proposal["evidence_item_ids"]:
                        connection.execute(
                            "INSERT INTO trade_proposal_evidence_refs(proposal_id,evidence_id,item_id) VALUES (%s,%s,%s)",
                            (proposal_id, proposal["evidence_id"], item_id),
                        )
                self._fail(AgentPersistenceStage.PROPOSAL, _fail_after)
                audit = result.audit
                connection.execute(
                    """
                    INSERT INTO analysis_audit_records(
                      audit_id,run_id,verdict,audit_hash,payload,created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        audit["audit_id"],
                        run_id,
                        audit["verdict"],
                        audit["audit_hash"],
                        Jsonb(audit),
                        created_at,
                    ),
                )
                self._fail(AgentPersistenceStage.AUDIT, _fail_after)
                for event in result.events:
                    connection.execute(
                        """
                        INSERT INTO analysis_run_events(
                          event_id,run_id,event_type,payload_hash,payload,occurred_at
                        ) VALUES (%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            event["event_id"],
                            run_id,
                            event["event_type"],
                            event["payload_hash"],
                            Jsonb(event),
                            _timestamp(event["occurred_at"]),
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO outbox_events(
                          event_id,event_type,payload,payload_hash,occurred_at,published_at,
                          aggregate_type,aggregate_id,aggregate_version
                        ) VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s)
                        """,
                        (
                            event["event_id"],
                            event["event_type"],
                            Jsonb(event),
                            event["payload_hash"],
                            _timestamp(event["occurred_at"]),
                            "analysis-run",
                            run_id,
                            1,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO agent_outbox_links(event_id,run_id) VALUES (%s,%s)",
                        (event["event_id"], run_id),
                    )
                self._fail(AgentPersistenceStage.OUTBOX, _fail_after)
        return PersistedAnalysis(
            created=True,
            run_id=run_id,
            proposal_id=proposal_id,
            audit_hash=cast(str, result.audit["audit_hash"]),
        )
