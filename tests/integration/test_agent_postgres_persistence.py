from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from agent_orchestrator import (
    AgentWorkflow,
    EvidenceContext,
    MockLlmProvider,
    Role,
    bind_test_risk_input,
)
from agent_orchestrator.canonical import canonical_hash
from agent_orchestrator.persistence import AgentPersistenceStage, PostgresAgentStore
from docker_infrastructure_lock import docker_infrastructure_lock
from risk_engine import evaluate_risk
from risk_engine.persistence import PostgresRiskStore
from test_risk_engine import risk_input


ROOT = Path(__file__).parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
AGENT_DATABASE_URL = "postgresql://woozoo_agent_orchestrator@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"DATABASE_URL": DATABASE_URL, "TRADING_MODE": "paper"}
NOW = "2026-07-20T00:00:00Z"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def _seed_evidence() -> EvidenceContext:
    session_id = str(uuid4())
    evidence_id = "e" * 64
    evidence_digest = "a" * 64
    item_id = "1" * 64
    raw_id = "2" * 64
    raw_hash = "3" * 64
    event_time = datetime(2026, 7, 20, tzinfo=UTC)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """
            INSERT INTO collector_sessions(id,source,connection_id,allowlist_version,status,started_at)
            VALUES (%s,'binance_spot_public',%s,
              'binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5',
              'healthy',%s)
            """,
            (session_id, f"agent-{session_id}", event_time),
        )
        connection.execute(
            """
            INSERT INTO raw_market_events(
              id,collector_session_id,source,stream,symbol,record_kind,source_dedupe_key,
              payload_bytes,payload_hash,source_event_time,received_at,ingested_at,sequence,
              schema_version
            ) VALUES (%s,%s,'binance_spot_public','btcusdt@trade','BTCUSDT',
              'stream_message','agent-seed',%s,%s,%s,%s,%s,1,'woozoo.raw-market-event/v1')
            """,
            (raw_id, session_id, b"{}", raw_hash, event_time, event_time, event_time),
        )
        connection.execute(
            """
            INSERT INTO normalized_market_events(
              id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,
              sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,
              stream_watermark,payload
            ) VALUES (%s,%s,'trade','woozoo.market-event/v1','binance_spot_public',
              'BTCUSDT',%s,%s,1,%s,%s,'healthy',%s,%s,%s)
            """,
            (
                item_id,
                raw_id,
                event_time,
                event_time,
                raw_hash,
                session_id,
                Jsonb([]),
                Jsonb(
                    {
                        "session_id": session_id,
                        "stream": "trade",
                        "last_sequence": 1,
                        "observed_at": NOW,
                    }
                ),
                Jsonb({"kind": "trade", "price": "100", "quantity": "1", "buyer_maker": False}),
            ),
        )
        connection.execute(
            """
            INSERT INTO evidence_snapshots(
              evidence_id,evidence_digest,symbol,as_of,knowledge_cutoff,recipe_version,
              input_digest,quality_status,quality_reasons,collector_session_id,
              watermark_digest,created_at
            ) VALUES (%s,%s,'BTCUSDT',%s,%s,
              'woozoo.evidence.closed-candles-approved-features/v1',%s,'healthy',%s,%s,%s,%s)
            """,
            (
                evidence_id,
                evidence_digest,
                event_time,
                event_time,
                "4" * 64,
                Jsonb([]),
                session_id,
                "5" * 64,
                event_time,
            ),
        )
        connection.execute(
            """
            INSERT INTO evidence_items(
              evidence_id,ordinal,item_type,item_id,normalized_event_id,
              feature_observation_id,raw_event_id,raw_payload_hash
            ) VALUES (%s,0,'normalized_market_event',%s,%s,NULL,%s,%s)
            """,
            (evidence_id, item_id, item_id, raw_id, raw_hash),
        )
    return EvidenceContext(
        evidence_id=evidence_id,
        evidence_digest=evidence_digest,
        symbol="BTCUSDT",
        as_of=NOW,
        knowledge_cutoff=NOW,
        quality="healthy",
        item_ids=(item_id,),
        quoted_content=('{"item_id":"' + item_id + '","item_type":"normalized_market_event"}',),
    )


def test_agent_persistence_is_atomic_idempotent_and_append_only() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            evidence = _seed_evidence()
            store = PostgresAgentStore(AGENT_DATABASE_URL)
            loaded = store.load_evidence(evidence.evidence_id)
            assert loaded is not None
            assert loaded.item_ids == evidence.item_ids
            assert len(loaded.quoted_content) == len(loaded.item_ids)
            assert '"price": "100"' in loaded.quoted_content[0]
            result = asyncio.run(AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(loaded))

            tampered = deepcopy(result)
            tampered.reports[0]["findings"][0] = "forged after workflow validation"
            with pytest.raises(ValueError, match="REPORT_HASH_MISMATCH"):
                store.persist(tampered)

            forged_evidence = replace(loaded, evidence_digest="b" * 64)
            forged = asyncio.run(
                AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(forged_evidence)
            )
            with pytest.raises(ValueError, match="AUTHORITATIVE_EVIDENCE_MISMATCH"):
                store.persist(forged)

            with pytest.raises(RuntimeError, match="INJECTED_AGENT_FAILURE:reports"):
                store.persist(result, _fail_after=AgentPersistenceStage.REPORTS)
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT count(*) FROM analysis_runs").fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM agent_reports").fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM agent_outbox_links").fetchone() == (
                    0,
                )

            first = store.persist(result)
            second = store.persist(result)
            assert first.created is True
            assert second.created is False
            assert first.run_id == second.run_id
            assert result.proposal is not None
            base_risk = risk_input()
            data = base_risk["data"]
            assert isinstance(data, dict)
            data["evidence_id"] = evidence.evidence_id
            data["evidence_hash"] = evidence.evidence_digest
            bound_risk = bind_test_risk_input(base_risk, result.proposal)
            risk_decision = evaluate_risk(bound_risk)
            persisted_risk = PostgresRiskStore(DATABASE_URL).persist_decision(
                bound_risk,
                risk_decision,
                recorded_at=datetime(2026, 7, 20, tzinfo=UTC),
            )
            assert persisted_risk.created is True
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT count(*) FROM analysis_runs").fetchone() == (1,)
                assert connection.execute("SELECT count(*) FROM agent_reports").fetchone() == (8,)
                assert connection.execute("SELECT count(*) FROM trade_proposals").fetchone() == (1,)
                assert connection.execute("SELECT count(*) FROM agent_outbox_links").fetchone() == (
                    2,
                )
                assert connection.execute(
                    "SELECT proposal_id FROM risk_decisions WHERE decision_id=%s",
                    (persisted_risk.decision_id,),
                ).fetchone() == (first.proposal_id,)
                with pytest.raises(psycopg.errors.RaiseException):
                    connection.execute(
                        "UPDATE trade_proposals SET side='HOLD' WHERE proposal_id=%s",
                        (first.proposal_id,),
                    )

            with pytest.raises(psycopg.errors.RaiseException, match="orphan Evidence citation"):
                with psycopg.connect(DATABASE_URL) as role_connection:
                    role_connection.execute("SET ROLE woozoo_agent_orchestrator")
                    role_connection.execute(
                        "INSERT INTO agent_report_evidence_refs(report_id,evidence_id,item_id) "
                        "VALUES (%s,%s,%s)",
                        (result.reports[0]["report_id"], evidence.evidence_id, "f" * 64),
                    )

            with psycopg.connect(DATABASE_URL) as connection:
                session_id = connection.execute(
                    "SELECT collector_session_id FROM evidence_snapshots WHERE evidence_id=%s",
                    (evidence.evidence_id,),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO evidence_snapshots(
                      evidence_id,evidence_digest,symbol,as_of,knowledge_cutoff,recipe_version,
                      input_digest,quality_status,quality_reasons,collector_session_id,
                      watermark_digest,created_at
                    ) VALUES (%s,%s,'BTCUSDT',%s,%s,
                      'woozoo.evidence.closed-candles-approved-features/v1',%s,'healthy',%s,%s,%s,%s)
                    """,
                    (
                        "d" * 64,
                        "b" * 64,
                        datetime(2026, 7, 20, tzinfo=UTC),
                        datetime(2026, 7, 20, tzinfo=UTC),
                        "6" * 64,
                        Jsonb([]),
                        session_id,
                        "7" * 64,
                        datetime(2026, 7, 20, tzinfo=UTC),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO evidence_items(
                      evidence_id,ordinal,item_type,item_id,normalized_event_id,
                      feature_observation_id,raw_event_id,raw_payload_hash
                    )
                    SELECT %s,0,'normalized_market_event',item_id,normalized_event_id,
                           NULL,raw_event_id,raw_payload_hash
                    FROM evidence_items WHERE evidence_id=%s AND ordinal=0
                    """,
                    ("d" * 64, evidence.evidence_id),
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with psycopg.connect(DATABASE_URL) as connection:
                    connection.execute(
                        "INSERT INTO trade_proposal_evidence_refs(proposal_id,evidence_id,item_id) "
                        "VALUES (%s,%s,%s)",
                        (first.proposal_id, "d" * 64, evidence.item_ids[0]),
                    )
        finally:
            run("docker", "compose", "down", "-v")


def test_agent_role_has_no_risk_paper_or_approval_table_privileges() -> None:
    migration = (ROOT / "db/migrations/versions/20260720_0006_agent_orchestrator.py").read_text(
        "utf-8"
    )
    assert "GRANT SELECT ON risk_decisions" not in migration
    assert "GRANT SELECT ON paper_orders" not in migration
    assert "paper_approvals" not in migration
    assert "evidence_reader_v1" in migration


def test_agent_persistence_rejects_hold_with_proposal() -> None:
    evidence = EvidenceContext(
        evidence_id="e" * 64,
        evidence_digest="a" * 64,
        symbol="BTCUSDT",
        as_of=NOW,
        knowledge_cutoff=NOW,
        quality="healthy",
        item_ids=("1" * 64,),
        quoted_content=("public market observation",),
    )
    contradictory = deepcopy(
        asyncio.run(AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(evidence))
    )
    assert contradictory.proposal is not None
    contradictory.run["outcome"] = "HOLD"
    contradictory.run["hold_reason"] = "AUDIT_REJECTED"
    completed_event = contradictory.events[0]
    completed_event["data"] = deepcopy(contradictory.run)
    completed_event["payload_hash"] = canonical_hash(completed_event["data"])
    completed_event["event_id"] = canonical_hash(
        {key: value for key, value in completed_event.items() if key != "event_id"}
    )
    with pytest.raises(Exception):
        PostgresAgentStore._validate_graph(contradictory)


def test_agent_persistence_rejects_rehashed_event_authority_mutation() -> None:
    evidence = EvidenceContext(
        evidence_id="e" * 64,
        evidence_digest="a" * 64,
        symbol="BTCUSDT",
        as_of=NOW,
        knowledge_cutoff=NOW,
        quality="healthy",
        item_ids=("1" * 64,),
        quoted_content=("public market observation",),
    )
    forged = deepcopy(
        asyncio.run(AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(evidence))
    )
    for event in forged.events:
        event["producer"] = "risk-engine"
        event["activation_phase"] = 6
        event["event_id"] = canonical_hash(
            {key: value for key, value in event.items() if key != "event_id"}
        )
    with pytest.raises(ValueError, match="EVENT_ENVELOPE_AUTHORITY_MISMATCH"):
        PostgresAgentStore._validate_graph(forged)


def test_agent_database_rejects_hold_proposal_child() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            evidence = _seed_evidence()
            store = PostgresAgentStore(AGENT_DATABASE_URL)
            loaded = store.load_evidence(evidence.evidence_id)
            assert loaded is not None
            held = asyncio.run(
                AgentWorkflow(
                    MockLlmProvider(failures=frozenset({Role.MARKET_REGIME})),
                    clock=lambda: NOW,
                ).run(loaded)
            )
            assert held.run["outcome"] == "HOLD"
            store.persist(held)
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="Proposal requires a matching completed run",
            ):
                with psycopg.connect(DATABASE_URL) as connection:
                    connection.execute(
                        """
                        INSERT INTO trade_proposals(
                          proposal_id,run_id,evidence_id,proposal_version,side,risk_eligible,
                          proposal_hash,payload,created_at
                        ) VALUES (%s,%s,%s,'v1','BUY',true,%s,%s,%s)
                        """,
                        (
                            "8" * 64,
                            held.run["run_id"],
                            evidence.evidence_id,
                            "9" * 64,
                            Jsonb({"proposal_id": "8" * 64}),
                            datetime(2026, 7, 20, tzinfo=UTC),
                        ),
                    )
        finally:
            run("docker", "compose", "down", "-v")
