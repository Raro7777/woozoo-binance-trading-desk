from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from agent_orchestrator import (
    AgentWorkflow,
    EvidenceContext,
    MockLlmProvider,
    Role,
    WorkflowResult,
    bind_test_risk_input,
)
from agent_orchestrator.canonical import canonical_hash
from agent_orchestrator.persistence import (
    AgentPersistenceStage,
    AnalysisCommandReceipt,
    PersistedAnalysis,
    PostgresAgentStore,
)
from control_api.command_ports import (
    CommandPortRejected,
    PostgresAgentAnalysisPort,
)
from control_api.trading_room import PostgresTradingRoom
from docker_infrastructure_lock import docker_infrastructure_lock
from paper_engine.authorization_worker import PostgresWorkerStateReporter
from paper_engine.persistence import Phase7AuthorizationWorker, PostgresPaperStore
from risk_engine import evaluate_risk
from risk_engine.persistence import PostgresRiskStore
from test_risk_engine import risk_input


ROOT = Path(__file__).parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
AGENT_DATABASE_URL = "postgresql://woozoo_agent_orchestrator@127.0.0.1:5433/woozoo"
RISK_DATABASE_URL = "postgresql://woozoo_risk_engine@127.0.0.1:5433/woozoo"
PAPER_DATABASE_URL = "postgresql://woozoo_paper_engine@127.0.0.1:5433/woozoo"
PAPER_ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"
ENVIRONMENT = {"DATABASE_URL": DATABASE_URL, "TRADING_MODE": "paper"}
NOW = "2026-07-20T00:00:00Z"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def _seed_evidence(
    *,
    symbol: str = "BTCUSDT",
    observed_at: datetime = datetime(2026, 7, 20, tzinfo=UTC),
    evidence_id: str = "e" * 64,
    evidence_digest: str = "a" * 64,
    item_id: str = "1" * 64,
    raw_id: str = "2" * 64,
    raw_hash: str = "3" * 64,
) -> EvidenceContext:
    session_id = str(uuid4())
    event_time = observed_at
    observed_text = observed_at.isoformat().replace("+00:00", "Z")
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
            ) VALUES (%s,%s,'binance_spot_public',%s,%s,
              'stream_message',%s,%s,%s,%s,%s,%s,1,'woozoo.raw-market-event/v1')
            """,
            (
                raw_id,
                session_id,
                f"{symbol.lower()}@trade",
                symbol,
                f"agent-seed-{symbol}-{evidence_id[:8]}",
                b"{}",
                raw_hash,
                event_time,
                event_time,
                event_time,
            ),
        )
        connection.execute(
            """
            INSERT INTO normalized_market_events(
              id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,
              sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,
              stream_watermark,payload
            ) VALUES (%s,%s,'trade','woozoo.market-event/v1','binance_spot_public',
              %s,%s,%s,1,%s,%s,'healthy',%s,%s,%s)
            """,
            (
                item_id,
                raw_id,
                symbol,
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
                        "observed_at": observed_text,
                    }
                ),
                Jsonb(
                    {
                        "kind": "trade",
                        "price": "100" if symbol == "BTCUSDT" else "50",
                        "quantity": "1",
                        "buyer_maker": False,
                    }
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO evidence_snapshots(
              evidence_id,evidence_digest,symbol,as_of,knowledge_cutoff,recipe_version,
              input_digest,quality_status,quality_reasons,collector_session_id,
              watermark_digest,created_at
            ) VALUES (%s,%s,%s,%s,%s,
              'woozoo.evidence.closed-candles-approved-features/v1',%s,'healthy',%s,%s,%s,%s)
            """,
            (
                evidence_id,
                evidence_digest,
                symbol,
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
        symbol=symbol,
        as_of=observed_text,
        knowledge_cutoff=observed_text,
        quality="healthy",
        item_ids=(item_id,),
        quoted_content=('{"item_id":"' + item_id + '","item_type":"normalized_market_event"}',),
    )


def _seed_phase7_books(
    evidence_id: str, observed_at: datetime = datetime(2026, 7, 20, tzinfo=UTC)
) -> None:
    observed_text = observed_at.isoformat().replace("+00:00", "Z")
    with psycopg.connect(DATABASE_URL) as connection:
        session_id = connection.execute(
            "SELECT collector_session_id FROM evidence_snapshots WHERE evidence_id=%s",
            (evidence_id,),
        ).fetchone()[0]
        for ordinal, (symbol, bid, ask) in enumerate(
            (("BTCUSDT", "99.99", "100"), ("ETHUSDT", "49.99", "50")), start=2
        ):
            raw_id = f"{ordinal + 4:x}" * 64
            normalized_id = f"{ordinal + 8:x}" * 64
            raw_hash = f"{ordinal + 10:x}" * 64
            connection.execute(
                """
                INSERT INTO raw_market_events(
                  id,collector_session_id,source,stream,symbol,record_kind,source_dedupe_key,
                  payload_bytes,payload_hash,source_event_time,received_at,ingested_at,sequence,
                  schema_version)
                VALUES (%s,%s,'binance_spot_public',%s,%s,'stream_message',%s,%s,%s,
                  %s,%s,%s,%s,'woozoo.raw-market-event/v1')
                """,
                (
                    raw_id,
                    str(session_id),
                    f"{symbol.lower()}@bookTicker",
                    symbol,
                    f"phase7-risk-book-{symbol}",
                    b"{}",
                    raw_hash,
                    observed_at,
                    observed_at,
                    observed_at,
                    ordinal,
                ),
            )
            connection.execute(
                """
                INSERT INTO normalized_market_events(
                  id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,
                  sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,
                  stream_watermark,payload)
                VALUES (%s,%s,'book_ticker','woozoo.market-event/v1','binance_spot_public',
                  %s,%s,%s,%s,%s,%s,'healthy',%s,%s,%s)
                """,
                (
                    normalized_id,
                    raw_id,
                    symbol,
                    observed_at,
                    observed_at,
                    ordinal,
                    raw_hash,
                    session_id,
                    Jsonb([]),
                    Jsonb(
                        {
                            "session_id": str(session_id),
                            "stream": "book_ticker",
                            "last_sequence": ordinal,
                            "observed_at": observed_text,
                        }
                    ),
                    Jsonb(
                        {
                            "kind": "book_ticker",
                            "bid_price": bid,
                            "bid_quantity": "1",
                            "ask_price": ask,
                            "ask_quantity": "1",
                            "event_time_source": "received_at",
                        }
                    ),
                ),
            )


def _seed_newer_evidence_copy(
    source_evidence_id: str,
    observed_at: datetime,
    *,
    evidence_id: str = "d" * 64,
    evidence_digest: str = "b" * 64,
) -> str:
    with psycopg.connect(DATABASE_URL) as connection:
        session_id = connection.execute(
            "SELECT collector_session_id FROM evidence_snapshots WHERE evidence_id=%s",
            (source_evidence_id,),
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
                evidence_id,
                evidence_digest,
                observed_at,
                observed_at,
                "6" * 64,
                Jsonb([]),
                session_id,
                "7" * 64,
                observed_at,
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
            (evidence_id, source_evidence_id),
        )
    return evidence_id


def test_concurrent_receipt_creation_replays_winner_across_evidence_advance() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            first_evidence = _seed_evidence()
            store = PostgresAgentStore(AGENT_DATABASE_URL)
            first_loaded = store.load_evidence(first_evidence.evidence_id)
            assert first_loaded is not None
            first_workflow = asyncio.run(
                AgentWorkflow(MockLlmProvider(), clock=lambda: NOW, namespace="paper").run(
                    first_loaded
                )
            )
            later = datetime(2026, 7, 20, 0, 1, tzinfo=UTC)
            later_text = later.isoformat().replace("+00:00", "Z")
            second_evidence_id = _seed_newer_evidence_copy(first_evidence.evidence_id, later)
            second_loaded = store.load_evidence(second_evidence_id)
            assert second_loaded is not None
            second_workflow = asyncio.run(
                AgentWorkflow(MockLlmProvider(), clock=lambda: later_text, namespace="paper").run(
                    second_loaded
                )
            )
            request_hash = canonical_hash(
                {
                    "method": "POST",
                    "path": "/api/v1/analysis-runs",
                    "actor": "operator-local-1",
                    "body": {"symbol": "BTCUSDT"},
                }
            )
            key = "phase7-concurrent-receipt-first"
            assert store.lookup_command_receipt(key, request_hash) is None
            barrier = Barrier(2)

            def persist_selected(workflow: WorkflowResult, evidence_id: str) -> PersistedAnalysis:
                barrier.wait(timeout=5)
                return store.persist(
                    workflow,
                    command_receipt=AnalysisCommandReceipt(key, request_hash, evidence_id),
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    persist_selected, first_workflow, first_evidence.evidence_id
                )
                second_future = executor.submit(
                    persist_selected, second_workflow, second_evidence_id
                )
                results = (first_future.result(timeout=15), second_future.result(timeout=15))
            assert results[0].run_id == results[1].run_id
            assert results[0].proposal_id == results[1].proposal_id
            assert results[0].outcome == results[1].outcome == "COMPLETED"
            assert sum(result.created for result in results) == 1
            replay = store.lookup_command_receipt(key, request_hash)
            assert replay is not None and replay.run_id == results[0].run_id

            selected = PostgresAgentAnalysisPort(AGENT_DATABASE_URL).analyze_latest(
                "BTCUSDT",
                later,
                "phase7-new-key-selects-new-evidence",
                request_hash,
            )
            assert selected.run_id == second_workflow.run["run_id"]

            hold_evidence_id = _seed_newer_evidence_copy(
                first_evidence.evidence_id,
                later + timedelta(seconds=1),
                evidence_id="c" * 64,
                evidence_digest="c" * 64,
            )
            hold_loaded = store.load_evidence(hold_evidence_id)
            assert hold_loaded is not None
            hold_loaded = replace(hold_loaded, quality="stale")
            hold_workflow = asyncio.run(
                AgentWorkflow(MockLlmProvider(), clock=lambda: later_text, namespace="paper").run(
                    hold_loaded
                )
            )
            assert first_workflow.run["outcome"] == "COMPLETED"
            assert hold_workflow.run["outcome"] == "HOLD"
            for winner, loser, winner_evidence_id, loser_evidence_id in (
                (
                    first_workflow,
                    hold_workflow,
                    first_evidence.evidence_id,
                    hold_evidence_id,
                ),
                (
                    hold_workflow,
                    first_workflow,
                    hold_evidence_id,
                    first_evidence.evidence_id,
                ),
            ):
                race_key = f"phase7-opposite-outcome-{winner.run['outcome'].lower()}-winner"
                selected_barrier = Barrier(2)
                winner_committed = Event()

                def persist_opposite_racer(
                    workflow: WorkflowResult,
                    evidence_id: str,
                    *,
                    is_winner: bool,
                ) -> PersistedAnalysis:
                    assert store.lookup_command_receipt(race_key, request_hash) is None
                    selected_barrier.wait(timeout=5)
                    if not is_winner:
                        assert winner_committed.wait(timeout=10)
                    try:
                        return store.persist(
                            workflow,
                            command_receipt=AnalysisCommandReceipt(
                                race_key, request_hash, evidence_id
                            ),
                        )
                    finally:
                        if is_winner:
                            winner_committed.set()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    winner_future = executor.submit(
                        persist_opposite_racer,
                        winner,
                        winner_evidence_id,
                        is_winner=True,
                    )
                    loser_future = executor.submit(
                        persist_opposite_racer,
                        loser,
                        loser_evidence_id,
                        is_winner=False,
                    )
                    winner_persisted = winner_future.result(timeout=15)
                    loser_replay = loser_future.result(timeout=15)
                assert loser_replay.created is False
                assert loser_replay.run_id == winner_persisted.run_id
                assert loser_replay.proposal_id == winner_persisted.proposal_id
                assert loser_replay.outcome == winner.run["outcome"]
                assert loser_replay.hold_reason == winner.run["hold_reason"]
        finally:
            run("docker", "compose", "down", "-v")


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
            command_receipt = AnalysisCommandReceipt(
                idempotency_key="phase7-analysis-receipt",
                request_hash=canonical_hash(
                    {
                        "method": "POST",
                        "path": "/api/v1/analysis-runs",
                        "actor": "operator-local-1",
                        "body": {"symbol": "BTCUSDT"},
                    }
                ),
                evidence_id=evidence.evidence_id,
            )
            with pytest.raises(RuntimeError, match="INJECTED_AGENT_FAILURE:receipt"):
                store.persist(
                    result,
                    command_receipt=command_receipt,
                    _fail_after=AgentPersistenceStage.RECEIPT,
                )
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT count(*) FROM analysis_runs").fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM agent_reports").fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM agent_outbox_links").fetchone() == (
                    0,
                )
                assert connection.execute(
                    "SELECT count(*) FROM agent_analysis_command_receipts"
                ).fetchone() == (0,)

            first = store.persist(result, command_receipt=command_receipt)
            second = store.persist(result, command_receipt=command_receipt)
            assert first.created is True
            assert second.created is False
            assert first.run_id == second.run_id
            with pytest.raises(ValueError, match="ANALYSIS_IDEMPOTENCY_CONFLICT"):
                store.persist(
                    result,
                    command_receipt=replace(
                        command_receipt,
                        request_hash=canonical_hash(
                            {
                                "method": "POST",
                                "path": "/api/v1/analysis-runs",
                                "actor": "operator-local-1",
                                "body": {"symbol": "ETHUSDT"},
                            }
                        ),
                    ),
                )
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
            newer_evidence = store.load_evidence("d" * 64)
            assert newer_evidence is not None
            newer_result = asyncio.run(
                AgentWorkflow(MockLlmProvider(), clock=lambda: NOW).run(newer_evidence)
            )
            assert newer_result.run["evidence_id"] == "d" * 64
            replayed_receipt = store.lookup_command_receipt(
                command_receipt.idempotency_key, command_receipt.request_hash
            )
            assert replayed_receipt is not None
            assert replayed_receipt.run_id == first.run_id
            assert replayed_receipt.proposal_id == first.proposal_id
            with pytest.raises(ValueError, match="ANALYSIS_IDEMPOTENCY_CONFLICT"):
                store.lookup_command_receipt(
                    command_receipt.idempotency_key,
                    canonical_hash(
                        {
                            "method": "POST",
                            "path": "/api/v1/analysis-runs",
                            "actor": "operator-local-1",
                            "body": {"symbol": "ETHUSDT"},
                        }
                    ),
                )
            port = PostgresAgentAnalysisPort(AGENT_DATABASE_URL)
            port_replay = port.analyze_latest(
                "BTCUSDT",
                datetime(2026, 7, 20, tzinfo=UTC),
                command_receipt.idempotency_key,
                command_receipt.request_hash,
            )
            assert port_replay.created is False
            assert port_replay.run_id == first.run_id
            assert port_replay.proposal_id == first.proposal_id

            class ReceiptReplayProjection(PostgresTradingRoom):
                def get_analysis(self, run_id: str) -> dict[str, object]:
                    return {
                        "run_id": run_id,
                        "proposal_id": first.proposal_id,
                        "risk_decision_id": "f" * 64,
                    }

            room_replay = ReceiptReplayProjection(
                "postgresql://control.invalid/woozoo", agent_analysis=port
            ).create_analysis("BTCUSDT", command_receipt.idempotency_key)
            assert room_replay.status_code == 200
            assert room_replay.body["run_id"] == first.run_id
            assert room_replay.body["proposal_id"] == first.proposal_id
            with pytest.raises(CommandPortRejected, match="Agent analysis held") as conflict:
                port.analyze_latest(
                    "ETHUSDT",
                    datetime(2026, 7, 20, tzinfo=UTC),
                    command_receipt.idempotency_key,
                    canonical_hash(
                        {
                            "method": "POST",
                            "path": "/api/v1/analysis-runs",
                            "actor": "operator-local-1",
                            "body": {"symbol": "ETHUSDT"},
                        }
                    ),
                )
            assert conflict.value.code == "ANALYSIS_IDEMPOTENCY_CONFLICT"
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with psycopg.connect(DATABASE_URL) as connection:
                    connection.execute(
                        "INSERT INTO trade_proposal_evidence_refs(proposal_id,evidence_id,item_id) "
                        "VALUES (%s,%s,%s)",
                        (first.proposal_id, "d" * 64, evidence.item_ids[0]),
                    )
        finally:
            run("docker", "compose", "down", "-v")


def test_phase7_postgres_evaluate_proposal_preserves_canonical_evidence_times() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            observed_at = datetime.now(UTC).replace(microsecond=0)
            observed_text = observed_at.isoformat().replace("+00:00", "Z")
            evidence = _seed_evidence(observed_at=observed_at)
            _seed_phase7_books(evidence.evidence_id, observed_at)
            PostgresPaperStore(DATABASE_URL).reconcile(
                PAPER_ACCOUNT_ID,
                checkpoint_id="f" * 64,
                created_at=observed_at,
            )
            agent_store = PostgresAgentStore(AGENT_DATABASE_URL)
            loaded = agent_store.load_evidence(evidence.evidence_id)
            assert loaded is not None
            workflow = asyncio.run(
                AgentWorkflow(
                    MockLlmProvider(), clock=lambda: observed_text, namespace="paper"
                ).run(loaded)
            )
            persisted_analysis = agent_store.persist(workflow)
            assert persisted_analysis.proposal_id is not None

            risk_input, decision, persisted_risk = PostgresRiskStore(
                RISK_DATABASE_URL
            ).evaluate_proposal(
                persisted_analysis.proposal_id,
                PAPER_ACCOUNT_ID,
                observed_at,
            )
            assert decision.verdict == "ALLOWED"
            assert "PROPOSAL_HASH_MISMATCH" not in decision.ordered_reason_codes
            data = risk_input["data"]
            assert isinstance(data, dict)
            assert data["as_of"] == observed_text
            assert data["knowledge_cutoff"] == observed_text
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT proposal_id,risk_input->'data'->>'as_of',verdict "
                    "FROM risk_decisions WHERE decision_id=%s",
                    (persisted_risk.decision_id,),
                ).fetchone() == (persisted_analysis.proposal_id, observed_text, "ALLOWED")
                connection.execute(
                    "INSERT INTO local_operators(actor_id,argon2id_phc,created_at) "
                    "VALUES ('operator-local-1',%s,%s)",
                    (
                        "$argon2id$v=19$m=65536,t=3,p=1$fixture$fixture",
                        observed_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO operator_sessions(session_digest,actor_id,issued_at,"
                    "last_seen_at,idle_expires_at,absolute_expires_at,revoked_at) "
                    "VALUES (%s,'operator-local-1',%s,%s,%s,%s,NULL)",
                    (
                        "1" * 64,
                        observed_at,
                        observed_at,
                        observed_at + timedelta(minutes=30),
                        observed_at + timedelta(hours=8),
                    ),
                )
                connection.execute(
                    "INSERT INTO session_csrf_tokens(csrf_token_digest,session_digest,"
                    "issued_at,expires_at,consumed_at) VALUES (%s,%s,%s,%s,%s)",
                    (
                        "2" * 64,
                        "1" * 64,
                        observed_at,
                        observed_at + timedelta(minutes=10),
                        observed_at,
                    ),
                )
            preview = risk_input["order_preview"]
            assert isinstance(preview, dict)
            PostgresWorkerStateReporter(
                PAPER_DATABASE_URL,
                instance_id="phase7-agent-persistence-approval-worker",
            ).start()
            approval_parameters = (
                "phase7-real-approval",
                "4" * 64,
                persisted_analysis.proposal_id,
                "APPROVED",
                1,
                preview["paper_order_preview_hash"],
                "operator-local-1",
                "1" * 64,
                "2" * 64,
                "3" * 64,
                "approval-nonce-00000000000000000000000000000001",
                "authorization-nonce-0000000000000000000000000001",
                observed_at,
            )
            with psycopg.connect(RISK_DATABASE_URL) as connection:
                first_approval = connection.execute(
                    "SELECT created,response FROM issue_paper_approval_v1("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    approval_parameters,
                ).fetchone()
                replayed_approval = connection.execute(
                    "SELECT created,response FROM issue_paper_approval_v1("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    approval_parameters,
                ).fetchone()
            assert first_approval is not None and replayed_approval is not None
            assert first_approval[0] is True and replayed_approval[0] is False
            assert first_approval[1] == replayed_approval[1]
            assert first_approval[1]["result"] == "APPROVED"
            assert first_approval[1]["authorization"]["proposal_id"] == (
                persisted_analysis.proposal_id
            )
            authorization_id = first_approval[1]["authorization"]["authorization_id"]
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="Paper execution authorization binding is incomplete",
            ):
                with psycopg.connect(DATABASE_URL) as connection:
                    connection.execute(
                        "ALTER TABLE paper_execution_authorizations DISABLE TRIGGER "
                        "paper_execution_authorizations_append_only"
                    )
                    connection.execute(
                        "WITH authz AS (DELETE FROM paper_execution_authorizations "
                        "WHERE authorization_id=%s RETURNING *) "
                        "INSERT INTO paper_execution_authorizations("
                        "authorization_id,namespace,approval_id,approval_hash,"
                        "approval_nonce_hash,authorization_nonce,proposal_id,proposal_hash,"
                        "risk_decision_id,risk_decision_hash,risk_input_digest,"
                        "risk_policy_version,paper_order_preview_hash,"
                        "authorization_input_digest,current_data_state_hash,current_data_as_of,"
                        "current_knowledge_cutoff,kill_switch_version,"
                        "reconciliation_checkpoint_hash,ledger_snapshot_hash,paper_account_id,"
                        "issued_at,expires_at) SELECT %s,authz.namespace,authz.approval_id,"
                        "authz.approval_hash,authz.approval_nonce_hash,approval.approval_nonce,"
                        "authz.proposal_id,authz.proposal_hash,authz.risk_decision_id,"
                        "authz.risk_decision_hash,authz.risk_input_digest,"
                        "authz.risk_policy_version,authz.paper_order_preview_hash,"
                        "authz.authorization_input_digest,authz.current_data_state_hash,"
                        "authz.current_data_as_of,authz.current_knowledge_cutoff,"
                        "authz.kill_switch_version,authz.reconciliation_checkpoint_hash,"
                        "authz.ledger_snapshot_hash,authz.paper_account_id,authz.issued_at,"
                        "authz.expires_at FROM authz "
                        "JOIN paper_approvals approval USING(approval_id)",
                        (authorization_id, "0" * 64),
                    )
                    connection.execute(
                        "SET CONSTRAINTS paper_execution_authorization_binding IMMEDIATE"
                    )

            paper_result = Phase7AuthorizationWorker(PostgresPaperStore(DATABASE_URL)).run_once()
            assert paper_result is not None
            assert paper_result.response["result"] == "CONSUMED_ORDER_CREATED"
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute(
                    "SELECT symbol,status FROM paper_orders WHERE account_id=%s",
                    (PAPER_ACCOUNT_ID,),
                ).fetchall() == [("BTCUSDT", "OPEN")]

            second_at = observed_at + timedelta(seconds=1)
            second_text = second_at.isoformat().replace("+00:00", "Z")
            second_evidence = _seed_evidence(
                symbol="ETHUSDT",
                observed_at=second_at,
                evidence_id="6" * 64,
                evidence_digest="7" * 64,
                item_id="8" * 64,
                raw_id="9" * 64,
                raw_hash="b" * 64,
            )
            loaded_second = agent_store.load_evidence(second_evidence.evidence_id)
            assert loaded_second is not None
            second_workflow = asyncio.run(
                AgentWorkflow(MockLlmProvider(), clock=lambda: second_text, namespace="paper").run(
                    loaded_second
                )
            )
            second_analysis = agent_store.persist(second_workflow)
            assert second_analysis.proposal_id is not None
            second_risk_input, second_decision, _ = PostgresRiskStore(
                RISK_DATABASE_URL
            ).evaluate_proposal(second_analysis.proposal_id, PAPER_ACCOUNT_ID, second_at)
            second_portfolio = second_risk_input["portfolio"]
            assert isinstance(second_portfolio, dict)
            open_orders = second_portfolio["open_orders"]
            assert isinstance(open_orders, list) and len(open_orders) == 1
            fee_text = open_orders[0]["remaining_worst_case_quote_fee"]
            assert isinstance(fee_text, str)
            assert len(fee_text.partition(".")[2]) <= 18
            assert second_decision.verdict == "ALLOWED"
            assert "INPUT_SCHEMA_INVALID" not in second_decision.ordered_reason_codes
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
