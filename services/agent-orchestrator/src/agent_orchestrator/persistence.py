"""Atomic append-only persistence for Phase 6 analysis results and outbox events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from .canonical import canonical_hash
from .models import EvidenceContext, WorkflowResult


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
                SELECT evidence_id,evidence_digest,symbol,as_of,knowledge_cutoff,
                       quality_status,item_id
                FROM evidence_reader_v1 WHERE evidence_id=%s ORDER BY item_ordinal
                """,
                (evidence_id,),
            ).fetchall()
        if not rows:
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
        )

    def persist(
        self,
        result: WorkflowResult,
        *,
        _fail_after: AgentPersistenceStage | None = None,
    ) -> PersistedAnalysis:
        run = result.run
        run_id = cast(str, run["run_id"])
        if result.proposal is not None and result.proposal["analysis_run_id"] != run_id:
            raise ValueError("PROPOSAL_RUN_MISMATCH")
        if (
            canonical_hash(
                {
                    key: value
                    for key, value in result.audit.items()
                    if key not in {"audit_id", "audit_hash"}
                }
            )
            != result.audit["audit_hash"]
        ):
            raise ValueError("AUDIT_HASH_MISMATCH")
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
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
