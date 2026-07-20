from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import inspect
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from agent_orchestrator import (
    AgentWorkflow,
    EvidenceContext,
    MockLlmProvider,
    bind_paper_risk_input,
)
from docker_infrastructure_lock import docker_infrastructure_lock
from paper_engine.authorization_worker import DurableRecordedBookWorker
from paper_engine.persistence import PersistenceStage, Phase7AuthorizationWorker, PostgresPaperStore
from platform_core import canonical_hash, canonical_json
from platform_core.generated_contracts import (
    PAPER_DOMAIN_EVENTS_V2_SCHEMA,
    PAPER_ORDER_V2_SCHEMA,
)
from risk_engine import KillActivation, PostgresKillSwitch, evaluate_risk
from test_risk_engine import risk_input as base_risk_input


ROOT = Path(__file__).parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
PAPER_WRITER_URL = "postgresql://woozoo_paper_engine@127.0.0.1:5433/woozoo"
ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}


def _validate_phase7_event(payload: object) -> None:
    registry = Registry().with_resource(
        "woozoo.paper-domain-events/paper-order.v2.json",
        Resource.from_contents(PAPER_ORDER_V2_SCHEMA),
    )
    Draft202012Validator(
        PAPER_DOMAIN_EVENTS_V2_SCHEMA,
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(payload)


def _seed_operator_csrf(suffix: str, observed_at: datetime) -> tuple[str, str]:
    session_digest = canonical_hash({"operator-session": suffix})
    csrf_digest = canonical_hash({"operator-csrf": suffix})
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "INSERT INTO local_operators(actor_id,argon2id_phc,created_at) "
            "VALUES ('operator-local-1',%s,%s)",
            ("$argon2id$" + "x" * 40, observed_at - timedelta(minutes=2)),
        )
        connection.execute(
            "INSERT INTO operator_sessions"
            "(session_digest,actor_id,issued_at,last_seen_at,idle_expires_at,"
            "absolute_expires_at,revoked_at) VALUES "
            "(%s,'operator-local-1',%s,%s,%s,%s,NULL)",
            (
                session_digest,
                observed_at - timedelta(minutes=2),
                observed_at - timedelta(minutes=1),
                observed_at + timedelta(minutes=9),
                observed_at + timedelta(hours=1),
            ),
        )
        connection.execute(
            "INSERT INTO session_csrf_tokens"
            "(csrf_token_digest,session_digest,issued_at,expires_at,consumed_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                csrf_digest,
                session_digest,
                observed_at - timedelta(minutes=1),
                observed_at + timedelta(minutes=9),
                observed_at - timedelta(seconds=1),
            ),
        )
    return session_digest, csrf_digest


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


@pytest.fixture()
def postgres() -> Iterator[None]:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            yield
        finally:
            run("docker", "compose", "down", "-v")


def _seed_authorization(
    suffix: str,
    *,
    healthy_reconciliation: bool,
    book_age_seconds: int = 0,
    insert_authority: bool = True,
) -> tuple[str, str]:
    store = PostgresPaperStore(PAPER_WRITER_URL)
    checkpoint_id = f"phase7-{suffix}"
    if healthy_reconciliation:
        checkpoint_input = store.semantic_digest(ACCOUNT_ID)
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            connection.execute(
                "INSERT INTO paper_reconciliation_checkpoints"
                "(checkpoint_id,account_id,input_digest,output_digest,status,mismatch_codes,"
                "created_at) VALUES (%s,%s,%s,%s,'HEALTHY','[]'::jsonb,%s)",
                (
                    checkpoint_id,
                    ACCOUNT_ID,
                    checkpoint_input,
                    canonical_hash({"input": checkpoint_input, "mismatches": []}),
                    datetime.now(UTC),
                ),
            )
        reconciliation_hash = canonical_hash(
            {"checkpoint_id": checkpoint_id, "health": "HEALTHY", "mismatch_codes": []}
        )
    else:
        # Risk binds a structurally healthy checkpoint reference, while the
        # execution-time authority check proves the referenced row is absent.
        reconciliation_hash = canonical_hash(
            {"checkpoint_id": checkpoint_id, "health": "HEALTHY", "mismatch_codes": []}
        )
    ledger_snapshot_hash = store.semantic_digest(ACCOUNT_ID)
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=5)
    authorization_id = canonical_hash({"authorization": suffix})
    request_hash = canonical_hash({"authorization_input": suffix})
    approval_id = canonical_hash({"approval": suffix})
    approval_hash = canonical_hash({"approval_payload": suffix})
    preview_without_hash: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "quantity": "0.001000000000000000",
        "limit_price": "10000.000000000000000000",
        "worst_case_fee": "0.010000000000000000",
        "worst_case_hold": "10.010000000000000000",
        "worst_case_notional": "10.010000000000000000",
        "best_bid": "9999.000000000000000000",
        "best_ask": "10000.000000000000000000",
        "expected_slippage_inputs": {"method": "limit-vs-book-v1"},
    }
    preview_hash = canonical_hash(preview_without_hash)
    preview = {**preview_without_hash, "paper_order_preview_hash": preview_hash}
    evidence_id = canonical_hash({"evidence": suffix})
    evidence_hash = canonical_hash({"evidence_payload": suffix})
    authorized_data: dict[str, object] = {
        "evidence_id": evidence_id,
        "evidence_hash": evidence_hash,
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "knowledge_cutoff": now.isoformat().replace("+00:00", "Z"),
        "freshness": "FRESH",
        "quality": "HEALTHY",
        "future_contamination": False,
        "watermark_complete": True,
    }
    evidence_time = now.isoformat().replace("+00:00", "Z")
    workflow = asyncio.run(
        AgentWorkflow(MockLlmProvider(), clock=lambda: evidence_time, namespace="paper").run(
            EvidenceContext(
                evidence_id=evidence_id,
                evidence_digest=evidence_hash,
                symbol="BTCUSDT",
                as_of=evidence_time,
                knowledge_cutoff=evidence_time,
                quality="healthy",
                item_ids=(canonical_hash({"evidence-item": suffix}),),
                quoted_content=("immutable public market observation",),
            )
        )
    )
    assert workflow.proposal is not None
    proposal = workflow.proposal
    proposal_id = str(proposal["proposal_id"])
    proposal_hash = str(proposal["proposal_hash"])
    risk_base = base_risk_input()
    risk_base["data"] = authorized_data
    risk_base["order_preview"] = preview
    portfolio = risk_base["portfolio"]
    assert isinstance(portfolio, dict)
    positions = portfolio["positions"]
    assert isinstance(positions, dict)
    positions["BTCUSDT"] = {"available": "0", "held": "0", "midpoint": "9999.5"}
    positions["ETHUSDT"] = {"available": "0", "held": "0", "midpoint": "2000"}
    risk_base["market_books"] = {
        "BTCUSDT": {"best_bid": "9999", "best_ask": "10000"},
        "ETHUSDT": {"best_bid": "1999", "best_ask": "2001"},
    }
    portfolio["snapshot_hash"] = canonical_hash(
        {key: value for key, value in portfolio.items() if key != "snapshot_hash"}
    )
    reconciliation = risk_base["reconciliation"]
    assert isinstance(reconciliation, dict)
    reconciliation.update(
        {
            "checkpoint_id": checkpoint_id,
            "health": "HEALTHY",
            "mismatch_codes": [],
        }
    )
    reconciliation["checkpoint_hash"] = reconciliation_hash
    clock = risk_base["decision_clock"]
    assert isinstance(clock, dict)
    clock["decision_as_of"] = evidence_time
    risk_input = bind_paper_risk_input(risk_base, proposal)
    risk_decision = evaluate_risk(risk_input)
    assert risk_decision.verdict == "ALLOWED"
    risk_input_digest = risk_decision.risk_input_digest
    risk_hash = risk_decision.decision_hash
    risk_id = canonical_hash(["risk-decision", risk_hash])
    data_state_hash = canonical_hash(authorized_data)
    bound_portfolio = risk_input["portfolio"]
    assert isinstance(bound_portfolio, dict)
    portfolio_snapshot_hash = str(bound_portfolio["snapshot_hash"])
    book_observed_at = now - timedelta(seconds=book_age_seconds)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO evidence_snapshots"
            "(evidence_id,evidence_digest,symbol,as_of,knowledge_cutoff,recipe_version,"
            "input_digest,quality_status,quality_reasons,collector_session_id,"
            "watermark_digest,created_at) VALUES (%s,%s,'BTCUSDT',%s,%s,"
            "'woozoo.evidence.closed-candles-approved-features/v1',%s,'healthy',"
            "'[]'::jsonb,'00000000-0000-0000-0000-000000000001',%s,%s)",
            (
                evidence_id,
                evidence_hash,
                now,
                now,
                canonical_hash({"evidence_input": suffix}),
                canonical_hash({"watermark": suffix}),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO evidence_snapshots"
            "(evidence_id,evidence_digest,symbol,as_of,knowledge_cutoff,recipe_version,"
            "input_digest,quality_status,quality_reasons,collector_session_id,"
            "watermark_digest,created_at) VALUES (%s,%s,'ETHUSDT',%s,%s,"
            "'woozoo.evidence.closed-candles-approved-features/v1',%s,'healthy',"
            "'[]'::jsonb,'00000000-0000-0000-0000-000000000001',%s,%s)",
            (
                canonical_hash({"eth-evidence": suffix}),
                canonical_hash({"eth-evidence-payload": suffix}),
                now,
                now,
                canonical_hash({"eth-evidence-input": suffix}),
                canonical_hash({"eth-watermark": suffix}),
                now,
            ),
        )
        for index, (book_symbol, bid, ask) in enumerate(
            (("BTCUSDT", "9999", "10000"), ("ETHUSDT", "1999", "2001")), start=1
        ):
            connection.execute(
                "INSERT INTO normalized_market_events"
                "(id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,"
                "sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,"
                "stream_watermark,payload) VALUES (%s,%s,'book_ticker',"
                "'woozoo.market-event/v1','binance_spot_public',%s,%s,%s,%s,%s,%s,"
                "'healthy','[]'::jsonb,%s,%s)",
                (
                    canonical_hash({"book": suffix, "symbol": book_symbol}),
                    canonical_hash({"raw": suffix, "symbol": book_symbol}),
                    book_symbol,
                    book_observed_at,
                    book_observed_at,
                    index,
                    canonical_hash({"raw_hash": suffix, "symbol": book_symbol}),
                    f"phase7-{suffix}-{book_symbol}",
                    Jsonb({"last_sequence": index}),
                    Jsonb({"bid_price": bid, "ask_price": ask}),
                ),
            )
        connection.execute(
            "INSERT INTO risk_decisions"
            "(decision_id,risk_input_digest,risk_input,decision_hash,verdict,primary_reason,"
            "ordered_reason_codes,policy_version,proposal_hash,portfolio_snapshot_hash,"
            "data_state_hash,paper_order_preview_hash,reconciliation_checkpoint_hash,"
            "kill_switch_version,decision_as_of,recorded_at,proposal_id) VALUES "
            "(%s,%s,%s,%s,'ALLOWED','RISK_ALLOWED',%s,'woozoo.risk-policy/v1',%s,%s,%s,"
            "%s,%s,0,%s,%s,%s)",
            (
                risk_id,
                risk_input_digest,
                Jsonb(risk_input),
                risk_hash,
                Jsonb(["RISK_ALLOWED"]),
                proposal_hash,
                portfolio_snapshot_hash,
                data_state_hash,
                preview_hash,
                reconciliation_hash,
                now,
                now,
                proposal_id,
            ),
        )
        risk_event_data = {
            "decision_schema_version": risk_decision.decision_schema_version,
            "decision_id": risk_id,
            "risk_input_digest": risk_input_digest,
            "decision_hash": risk_hash,
            "verdict": risk_decision.verdict,
            "primary_reason": risk_decision.primary_reason_code,
            "ordered_reason_codes": list(risk_decision.ordered_reason_codes),
            "policy_version": "woozoo.risk-policy/v1",
            "proposal_hash": proposal_hash,
            "portfolio_snapshot_hash": portfolio_snapshot_hash,
            "data_state_hash": data_state_hash,
            "paper_order_preview_hash": preview_hash,
            "reconciliation_checkpoint_hash": reconciliation_hash,
            "kill_switch_version": 0,
            "decision_as_of": now.isoformat(),
        }
        risk_event_id = canonical_hash(["event", "risk.decision.recorded.v2", risk_id, "1"])
        risk_event_payload_hash = canonical_hash(risk_event_data)
        connection.execute(
            "INSERT INTO outbox_events"
            "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
            "aggregate_id,aggregate_version) VALUES "
            "(%s,'risk.decision.recorded.v2',%s,%s,%s,'risk_decision',%s,1)",
            (
                risk_event_id,
                Jsonb(
                    {
                        "spec_version": "woozoo.event/v1",
                        "event_id": risk_event_id,
                        "event_type": "risk.decision.recorded.v2",
                        "event_version": 2,
                        "occurred_at": now.isoformat(),
                        "producer": "risk-engine",
                        "activation_phase": 7,
                        "aggregate_id": risk_id,
                        "aggregate_version": 1,
                        "payload_hash": risk_event_payload_hash,
                        "data": risk_event_data,
                    }
                ),
                risk_event_payload_hash,
                now,
                risk_id,
            ),
        )
        connection.execute(
            "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
            "VALUES (%s,'risk-decision',%s)",
            (risk_event_id, risk_id),
        )
        if not insert_authority:
            connection.execute(
                "INSERT INTO trade_proposals"
                "(proposal_id,run_id,evidence_id,proposal_version,side,risk_eligible,"
                "proposal_hash,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    proposal_id,
                    proposal["analysis_run_id"],
                    proposal["evidence_id"],
                    proposal["proposal_version"],
                    proposal["side"],
                    proposal["risk_eligible"],
                    proposal_hash,
                    Jsonb(proposal),
                    now,
                ),
            )
            connection.execute("SET session_replication_role='origin'")
            return authorization_id, request_hash
        connection.execute(
            "INSERT INTO paper_approvals"
            "(approval_id,proposal_id,proposal_hash,risk_decision_id,risk_decision_hash,"
            "risk_input_digest,risk_policy_version,paper_order_preview,"
            "paper_order_preview_hash,actor_id,session_digest,csrf_token_digest,origin_hash,"
            "decision,approval_nonce,expected_kill_switch_version,expected_portfolio_version,"
            "expected_ledger_version,decided_at,expires_at,payload_hash) VALUES "
            "(%s,%s,%s,%s,%s,%s,'woozoo.risk-policy/v1',%s,%s,'operator-local-1',%s,%s,%s,"
            "'APPROVED',%s,0,0,0,%s,%s,%s)",
            (
                approval_id,
                proposal_id,
                proposal_hash,
                risk_id,
                risk_hash,
                risk_input_digest,
                Jsonb(preview),
                preview_hash,
                canonical_hash({"session": suffix}),
                canonical_hash({"csrf": suffix}),
                canonical_hash({"origin": suffix}),
                f"approval-nonce-{suffix}-0000000000000000",
                now,
                expires_at,
                approval_hash,
            ),
        )
        connection.execute(
            "INSERT INTO paper_execution_authorizations"
            "(authorization_id,namespace,approval_id,approval_hash,approval_nonce_hash,"
            "authorization_nonce,proposal_id,proposal_hash,risk_decision_id,risk_decision_hash,"
            "risk_input_digest,risk_policy_version,paper_order_preview_hash,"
            "authorization_input_digest,current_data_state_hash,current_data_as_of,"
            "current_knowledge_cutoff,kill_switch_version,reconciliation_checkpoint_hash,"
            "ledger_snapshot_hash,paper_account_id,issued_at,expires_at) VALUES "
            "(%s,'paper',%s,%s,%s,%s,%s,%s,%s,%s,%s,'woozoo.risk-policy/v1',%s,%s,%s,%s,%s,"
            "0,%s,%s,%s,%s,%s)",
            (
                authorization_id,
                approval_id,
                approval_hash,
                canonical_hash({"approval_nonce": suffix}),
                f"authorization-nonce-{suffix}-000000000000",
                proposal_id,
                proposal_hash,
                risk_id,
                risk_hash,
                risk_input_digest,
                preview_hash,
                request_hash,
                data_state_hash,
                now,
                now,
                reconciliation_hash,
                ledger_snapshot_hash,
                ACCOUNT_ID,
                now,
                expires_at,
            ),
        )
        approval_event_id = canonical_hash(
            ["event", "paper.approval.recorded.v1", approval_id, "1"]
        )
        approval_data = {"approval_id": approval_id, "payload_hash": approval_hash}
        approval_payload_hash = canonical_hash(approval_data)
        connection.execute(
            "INSERT INTO outbox_events"
            "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
            "aggregate_id,aggregate_version) VALUES "
            "(%s,'paper.approval.recorded.v1',%s,%s,%s,'paper_approval',%s,1)",
            (
                approval_event_id,
                Jsonb(
                    {
                        "spec_version": "woozoo.event/v1",
                        "event_id": approval_event_id,
                        "event_type": "paper.approval.recorded.v1",
                        "event_version": 2,
                        "occurred_at": now.isoformat(),
                        "producer": "risk-engine",
                        "activation_phase": 7,
                        "aggregate_id": approval_id,
                        "aggregate_version": 1,
                        "payload_hash": approval_payload_hash,
                        "data": approval_data,
                    }
                ),
                approval_payload_hash,
                now,
                approval_id,
            ),
        )
        connection.execute(
            "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
            "VALUES (%s,'paper-approval',%s)",
            (approval_event_id, approval_id),
        )
        issued_event_id = canonical_hash(
            ["event", "paper.authorization.issued.v1", authorization_id, "1"]
        )
        issued_data = {
            "authorization_id": authorization_id,
            "authorization_input_digest": request_hash,
        }
        issued_payload_hash = canonical_hash(issued_data)
        connection.execute(
            "INSERT INTO outbox_events"
            "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
            "aggregate_id,aggregate_version) VALUES "
            "(%s,'paper.authorization.issued.v1',%s,%s,%s,'paper_authorization',%s,1)",
            (
                issued_event_id,
                Jsonb(
                    {
                        "spec_version": "woozoo.event/v1",
                        "event_id": issued_event_id,
                        "event_type": "paper.authorization.issued.v1",
                        "event_version": 2,
                        "occurred_at": now.isoformat(),
                        "producer": "risk-engine",
                        "activation_phase": 7,
                        "aggregate_id": authorization_id,
                        "aggregate_version": 1,
                        "payload_hash": issued_payload_hash,
                        "data": issued_data,
                    }
                ),
                issued_payload_hash,
                now,
                authorization_id,
            ),
        )
        connection.execute(
            "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
            "VALUES (%s,'paper-authorization',%s)",
            (issued_event_id, authorization_id),
        )
        connection.execute("SET session_replication_role='origin'")
    return authorization_id, request_hash


def _record_current_public_book(
    suffix: str,
    *,
    sequence: int,
    symbol: str = "BTCUSDT",
    bid_price: str = "9999",
    bid_quantity: str = "0.00010000",
    ask_price: str = "10000",
    ask_quantity: str = "0.00010000",
    normalized_quality_reasons: tuple[str, ...] = (),
    market_quality_reasons: tuple[str, ...] = (),
    raw_payload_bytes: bytes | None = None,
    raw_payload_hash: str | None = None,
) -> tuple[str, datetime]:
    raw_event_id = canonical_hash({"recorded-book-raw": suffix})
    session_id = "00000000-0000-7000-8000-000000000777"
    stream = f"{symbol.lower()}@bookTicker"
    with psycopg.connect(DATABASE_URL) as connection:
        observed_row = connection.execute("SELECT clock_timestamp()").fetchone()
        assert observed_row is not None
        observed_at = observed_row[0]
        watermark = {
            "session_id": session_id,
            "stream": stream,
            "last_sequence": sequence,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        }
        connection.execute(
            "INSERT INTO collector_sessions"
            "(id,source,connection_id,allowlist_version,status,started_at,ended_at) "
            "VALUES (%s,'binance_spot_public','phase7-recorded-book',"
            "'binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5',"
            "'degraded',%s,NULL) ON CONFLICT (id) DO UPDATE SET "
            "status='degraded',ended_at=NULL",
            (session_id, observed_at - timedelta(minutes=1)),
        )
        canonical_raw_payload = canonical_json(
            {
                "u": sequence,
                "s": symbol,
                "b": bid_price,
                "B": bid_quantity,
                "a": ask_price,
                "A": ask_quantity,
            }
        ).encode("utf-8")
        payload_bytes = raw_payload_bytes or canonical_raw_payload
        payload_hash = raw_payload_hash or sha256(payload_bytes).hexdigest()
        market_event_id = sha256(
            (f"woozoo.market-event/v1|{raw_event_id}|book_ticker|{sequence}").encode("utf-8")
        ).hexdigest()
        connection.execute(
            "INSERT INTO raw_market_events"
            "(id,collector_session_id,source,stream,symbol,record_kind,parent_raw_event_id,"
            "source_dedupe_key,payload_bytes,payload_hash,source_event_time,received_at,"
            "ingested_at,sequence,schema_version) VALUES "
            "(%s,%s,'binance_spot_public',%s,%s,'stream_message',NULL,%s,%s,%s,"
            "%s,%s,%s,%s,'woozoo.raw-market-event/v1')",
            (
                raw_event_id,
                session_id,
                stream,
                symbol,
                f"book_ticker:{symbol}:{sequence}",
                payload_bytes,
                payload_hash,
                None,
                observed_at,
                observed_at,
                sequence,
            ),
        )
        connection.execute(
            "INSERT INTO normalized_market_events"
            "(id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,"
            "sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,"
            "stream_watermark,payload) VALUES (%s,%s,'book_ticker',"
            "'woozoo.market-event/v1','binance_spot_public',%s,%s,%s,%s,%s,%s,"
            "'healthy',%s,%s,%s)",
            (
                market_event_id,
                raw_event_id,
                symbol,
                observed_at,
                observed_at,
                sequence,
                payload_hash,
                session_id,
                Jsonb(list(normalized_quality_reasons)),
                Jsonb(watermark),
                Jsonb(
                    {
                        "kind": "book_ticker",
                        "bid_price": bid_price,
                        "bid_quantity": bid_quantity,
                        "ask_price": ask_price,
                        "ask_quantity": ask_quantity,
                        "event_time_source": "received_at",
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO stream_watermark_projections"
            "(collector_session_id,stream,last_sequence,observed_at,quality_status) "
            "VALUES (%s,%s,%s,%s,'healthy') ON CONFLICT (collector_session_id,stream) "
            "DO UPDATE SET last_sequence=EXCLUDED.last_sequence,"
            "observed_at=EXCLUDED.observed_at,quality_status='healthy'",
            (session_id, stream, sequence, observed_at),
        )
        connection.execute(
            "INSERT INTO market_status_projections"
            "(symbol,price,event_time,received_at,quality_status,quality_reasons,"
            "stream_watermark,last_event_id) VALUES "
            "(%s,%s,%s,%s,'healthy',%s,%s,%s) "
            "ON CONFLICT (symbol) DO UPDATE SET price=EXCLUDED.price,"
            "event_time=EXCLUDED.event_time,received_at=EXCLUDED.received_at,"
            "quality_status='healthy',quality_reasons=EXCLUDED.quality_reasons,"
            "stream_watermark=EXCLUDED.stream_watermark,last_event_id=EXCLUDED.last_event_id",
            (
                symbol,
                bid_price,
                observed_at,
                observed_at,
                Jsonb(list(market_quality_reasons)),
                Jsonb(watermark),
                market_event_id,
            ),
        )
    return market_event_id, observed_at


def _record_current_public_books(suffix: str, *, sequence: int) -> None:
    _record_current_public_book(f"{suffix}-btc", sequence=sequence, symbol="BTCUSDT")
    _record_current_public_book(
        f"{suffix}-eth",
        sequence=sequence,
        symbol="ETHUSDT",
        bid_price="1999",
        bid_quantity="0.00100000",
        ask_price="2001",
        ask_quantity="0.00100000",
    )


def _assert_blocked_only(authorization_id: str, reason_code: str) -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT outcome,reason_code FROM paper_authorization_attempts "
            "WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone() == ("BLOCKED", reason_code)
        assert connection.execute(
            "SELECT outcome,paper_order_id,response->>'reason_code' "
            "FROM paper_command_receipts WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone() == ("REJECTED", None, reason_code)
        assert connection.execute("SELECT count(*) FROM paper_orders").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM paper_fills").fetchone() == (0,)
        assert connection.execute(
            "SELECT asset,available,held,version FROM paper_asset_balances "
            "WHERE account_id=%s ORDER BY asset",
            (ACCOUNT_ID,),
        ).fetchall() == [
            ("USDT", 10000, 0, 0),
        ]
        assert connection.execute(
            "SELECT count(*) FROM paper_ledger_transactions WHERE account_id=%s",
            (ACCOUNT_ID,),
        ).fetchone() == (1,)
        events = connection.execute(
            "SELECT event_type,payload FROM paper_outbox_events_v1 "
            "WHERE account_id=%s ORDER BY event_type",
            (ACCOUNT_ID,),
        ).fetchall()
        assert [(event_type, payload["data"]["outcome"]) for event_type, payload in events] == [
            ("paper.authorization.blocked.v2", "BLOCKED")
        ]
        for _, payload in events:
            _validate_phase7_event(payload)


def test_approval_rejects_checkpoint_that_predates_a_paper_effect(
    postgres: None,
) -> None:
    drift_authorization_id, _ = _seed_authorization(
        "approval-authority-drift-effect", healthy_reconciliation=True
    )
    pending_authorization_id, _ = _seed_authorization(
        "approval-authority-drift-pending", healthy_reconciliation=False
    )
    first_effect = PostgresPaperStore(PAPER_WRITER_URL).attempt_phase7_authorization(
        drift_authorization_id
    )
    assert first_effect.created is True
    _seed_authorization(
        "approval-authority-drift-target",
        healthy_reconciliation=True,
        insert_authority=False,
    )
    target_evidence_id = canonical_hash({"evidence": "approval-authority-drift-target"})
    with psycopg.connect(DATABASE_URL) as connection:
        target = connection.execute(
            "SELECT proposal_id,paper_order_preview_hash,decision_as_of,"
            "reconciliation_checkpoint_hash "
            "FROM risk_decisions WHERE risk_input->'data'->>'evidence_id'=%s",
            (target_evidence_id,),
        ).fetchone()
    assert target is not None

    drift_effect = PostgresPaperStore(PAPER_WRITER_URL).attempt_phase7_authorization(
        pending_authorization_id
    )
    assert drift_effect.created is True
    assert drift_effect.response["result"] == "BLOCKED"
    with psycopg.connect(DATABASE_URL) as connection:
        health = connection.execute(
            "SELECT reconciliation_status,checkpoint_authority_sequence,"
            "current_authority_sequence FROM paper_health_reader_v1"
        ).fetchone()
        checkpoint_authority = connection.execute(
            "SELECT status,mismatch_codes,"
            "encode(digest(convert_to(risk_canonical_jsonb(jsonb_build_object("
            "'checkpoint_id',checkpoint_id,'health',status,"
            "'mismatch_codes',mismatch_codes)),'UTF8'),'sha256'),'hex') "
            "FROM paper_reconciliation_checkpoints WHERE account_id=%s "
            "ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
            (ACCOUNT_ID,),
        ).fetchone()
        blocked_link_count = connection.execute(
            "SELECT count(*) FROM paper_outbox_links link "
            "JOIN outbox_events event USING(event_id) "
            "WHERE link.account_id=%s AND event.event_type='paper.authorization.blocked.v2' "
            "AND event.aggregate_id=%s",
            (ACCOUNT_ID, pending_authorization_id),
        ).fetchone()
    assert health is not None
    assert health[0] == "STALE" and health[1] < health[2]
    assert checkpoint_authority == ("HEALTHY", [], target[3])
    assert blocked_link_count == (1,)

    decided_at = max(datetime.now(UTC), target[2])
    session_digest, csrf_digest = _seed_operator_csrf("approval-authority-drift-target", decided_at)
    idempotency_key = "phase7-approval-authority-drift"
    with pytest.raises(psycopg.errors.RaiseException, match="RECONCILIATION_DRIFT"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT created,response FROM issue_paper_approval_v1("
                "%s,%s,%s,'APPROVED',1,%s,'operator-local-1',%s,%s,%s,%s,%s,%s)",
                (
                    idempotency_key,
                    canonical_hash({"approval-authority-drift": target[0]}),
                    target[0],
                    target[1],
                    session_digest,
                    csrf_digest,
                    canonical_hash({"origin": "approval-authority-drift"}),
                    "approval-authority-drift-nonce-00000001",
                    "authorization-authority-drift-nonce-0001",
                    decided_at,
                ),
            )
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_approvals WHERE proposal_id=%s", (target[0],)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_execution_authorizations WHERE proposal_id=%s",
            (target[0],),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM risk_approval_command_receipts WHERE idempotency_key=%s",
            (idempotency_key,),
        ).fetchone() == (0,)


def test_kill_first_attempt_stays_blocked_after_recovery_and_retry(
    postgres: None,
) -> None:
    authorization_id, _request_hash = _seed_authorization(
        "kill-first-attempt", healthy_reconciliation=True
    )
    PostgresKillSwitch(DATABASE_URL).activate(
        KillActivation(
            request_id="phase7-kill-first-attempt",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="operator:phase7-test",
            reason_code="MANUAL_SAFETY_STOP",
            reason="prove first-attempt authority is terminal",
            observed_at=datetime.now(UTC),
            context_digest=canonical_hash({"test": "phase7-first-attempt"}),
        )
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    first = store.attempt_phase7_authorization(authorization_id)
    assert first.created is True
    assert first.response["reason_code"] == "KILL_SWITCH_ACTIVE"
    _assert_blocked_only(authorization_id, "KILL_SWITCH_ACTIVE")

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "UPDATE kill_switch_state SET active=false,version=2,last_recovery_event_id=%s "
            "WHERE scope='paper-global'",
            (canonical_hash({"recovery": "phase7-first-attempt"}),),
        )
        connection.execute("SET session_replication_role='origin'")
    replay = store.attempt_phase7_authorization(authorization_id)
    assert replay.created is False
    assert replay.response == first.response
    _assert_blocked_only(authorization_id, "KILL_SWITCH_ACTIVE")


def test_missing_reconciliation_consumes_authorization_without_financial_effects(
    postgres: None,
) -> None:
    authorization_id, _request_hash = _seed_authorization(
        "missing-reconciliation", healthy_reconciliation=False
    )
    worker = Phase7AuthorizationWorker(PostgresPaperStore(PAPER_WRITER_URL))
    result = worker.run_once()
    assert result is not None
    assert result.created is True
    assert result.response["reason_code"] == "RECONCILIATION_MISSING"
    _assert_blocked_only(authorization_id, "RECONCILIATION_MISSING")
    assert worker.run_once() is None


def test_authorized_order_and_cancel_are_atomic_v2_and_idempotent(postgres: None) -> None:
    authorization_id, _ = _seed_authorization("create-cancel", healthy_reconciliation=True)
    store = PostgresPaperStore(PAPER_WRITER_URL)
    created = store.attempt_phase7_authorization(authorization_id)
    assert created.created is True
    order_id = str(created.response["order_id"])
    cancel_hash = canonical_hash(
        {
            "idempotency_key": "phase7-cancel-create-cancel",
            "order_id": order_id,
            "expected_version": 1,
            "reason": "operator requested cancellation",
        }
    )
    cancelled = store.cancel_phase7_order(
        order_id,
        1,
        "phase7-cancel-create-cancel",
        cancel_hash,
        "operator requested cancellation",
    )
    assert cancelled.created is True
    assert cancelled.response["status"] == "CANCELLED"
    replay = store.cancel_phase7_order(
        order_id,
        1,
        "phase7-cancel-create-cancel",
        cancel_hash,
        "operator requested cancellation",
    )
    assert replay.created is False
    assert replay.response == cancelled.response
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,held_amount,version FROM paper_orders WHERE order_id=%s", (order_id,)
        ).fetchone() == ("CANCELLED", 0, 2)
        assert connection.execute(
            "SELECT available,held,version FROM paper_asset_balances "
            "WHERE account_id=%s AND asset='USDT'",
            (ACCOUNT_ID,),
        ).fetchone() == (10000, 0, 2)
        release = connection.execute(
            "SELECT transaction_id,journal_kind FROM paper_ledger_transactions "
            "WHERE account_id=%s AND business_event_type='paper.hold-release' "
            "AND business_event_id=%s",
            (ACCOUNT_ID, order_id),
        ).fetchone()
        assert release is not None
        release_id, journal_kind = release
        assert journal_kind == "PHYSICAL"
        assert connection.execute(
            "SELECT account_code,commodity,debit,credit FROM paper_ledger_entries "
            "WHERE transaction_id=%s ORDER BY line_no",
            (release_id,),
        ).fetchall() == [
            ("paper.available", "USDT", Decimal("10.01"), Decimal(0)),
            ("paper.held", "USDT", Decimal(0), Decimal("10.01")),
        ]
        assert connection.execute(
            "SELECT count(*),min(response->>'status') FROM paper_cancel_command_receipts "
            "WHERE idempotency_key='phase7-cancel-create-cancel'"
        ).fetchone() == (1, "CANCELLED")
        events = connection.execute(
            "SELECT event_type,payload FROM paper_outbox_events_v1 WHERE account_id=%s "
            "ORDER BY occurred_at,event_id",
            (ACCOUNT_ID,),
        ).fetchall()
        assert len(events) == 5
        assert sorted(event_type for event_type, _ in events) == [
            "ledger.transaction.posted.v2",
            "ledger.transaction.posted.v2",
            "paper.authorization.consumed.v2",
            "paper.order.accepted.v2",
            "paper.order.cancelled.v2",
        ]
        cancellation = next(
            payload for event_type, payload in events if event_type == "paper.order.cancelled.v2"
        )
        assert set(cancellation["data"]) == {"order", "request_hash", "reason"}
        assert cancellation["data"]["request_hash"] == cancel_hash
        assert cancellation["data"]["reason"] == "operator requested cancellation"
        assert cancellation["data"]["order"]["order_id"] == order_id
        assert cancellation["data"]["order"]["status"] == "CANCELLED"
        assert cancellation["data"]["order"]["version"] == 2
        assert any(
            payload["data"] == {"transaction_id": release_id, "authorization_id": authorization_id}
            for event_type, payload in events
            if event_type == "ledger.transaction.posted.v2"
        )
        for _, payload in events:
            _validate_phase7_event(payload)


def test_authorized_order_recorded_book_partial_fill_then_cancel_is_atomic_and_idempotent(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization(
        "recorded-book-partial-cancel", healthy_reconciliation=True
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    created = store.attempt_phase7_authorization(authorization_id)
    assert created.response["result"] == "CONSUMED_ORDER_CREATED"
    order_id = str(created.response["order_id"])

    stale_digest_event_id, _ = _record_current_public_book(
        "recorded-book-reconciliation-hold", sequence=9001
    )
    reconciliation_hold = store.apply_phase7_recorded_book(order_id, stale_digest_event_id)
    assert reconciliation_hold.created is False
    assert reconciliation_hold.response == {
        "result": "RECORDED_BOOK_HOLD",
        "reason_code": "PAPER_RECONCILIATION_HOLD",
        "market_event_id": stale_digest_event_id,
        "order_id": order_id,
    }
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_fills WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)

    opening_checkpoint = store.reconcile(
        ACCOUNT_ID,
        checkpoint_id="phase7-recorded-book-open",
        created_at=datetime.now(UTC),
    )
    assert opening_checkpoint.status == "HEALTHY"

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SELECT pg_sleep(5.2)")

    expired_hold = store.apply_phase7_recorded_book(order_id, stale_digest_event_id)
    assert expired_hold.response["reason_code"] == "INVALID_RECORDED_BOOK_INPUT"
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)

    market_event_id, book_at = _record_current_public_book(
        "recorded-book-partial-cancel", sequence=9002
    )

    with pytest.raises(RuntimeError, match="INJECTED_PAPER_FAILURE:outbox"):
        store.apply_phase7_recorded_book(
            order_id, market_event_id, _fail_after=PersistenceStage.OUTBOX
        )
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,filled_quantity,version FROM paper_orders WHERE order_id=%s",
            (order_id,),
        ).fetchone() == ("OPEN", Decimal(0), 1)
        assert connection.execute(
            "SELECT count(*) FROM paper_broker_inputs WHERE source_key=%s",
            (f"{market_event_id}:ASK",),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_fills WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)

    worker = DurableRecordedBookWorker(store)
    applied = worker.run_once()
    assert applied is not None and applied.created is True
    assert applied.response == {
        "result": "OBSERVATION_APPLIED",
        "market_event_id": market_event_id,
        "order_id": order_id,
        "status": "PARTIALLY_FILLED",
        "fill_id": applied.response["fill_id"],
    }
    replay = PostgresPaperStore(PAPER_WRITER_URL).apply_phase7_recorded_book(
        order_id, market_event_id
    )
    assert replay.created is False
    assert replay.response == applied.response
    assert DurableRecordedBookWorker(PostgresPaperStore(PAPER_WRITER_URL)).run_once() is None

    partial_checkpoint = store.reconcile(
        ACCOUNT_ID,
        checkpoint_id="phase7-recorded-book-partial",
        created_at=datetime.now(UTC),
    )
    assert partial_checkpoint.status == "HEALTHY"
    cancel_key = "phase7-cancel-recorded-book-partial"
    cancel_hash = canonical_hash(
        {
            "idempotency_key": cancel_key,
            "order_id": order_id,
            "expected_version": 2,
            "reason": "operator cancelled remaining partial quantity",
        }
    )
    cancelled = store.cancel_phase7_order(
        order_id,
        2,
        cancel_key,
        cancel_hash,
        "operator cancelled remaining partial quantity",
    )
    assert cancelled.response == {
        "result": "ORDER_CANCELLED",
        "order_id": order_id,
        "status": "CANCELLED",
        "version": 3,
    }
    post_cancel_replay = PostgresPaperStore(PAPER_WRITER_URL).apply_phase7_recorded_book(
        order_id, market_event_id
    )
    assert post_cancel_replay.created is False
    assert post_cancel_replay.response == applied.response
    cancelled_race_event_id, _ = _record_current_public_book(
        "recorded-book-cancel-race", sequence=9003
    )
    cancelled_race_hold = store.apply_phase7_recorded_book(order_id, cancelled_race_event_id)
    assert cancelled_race_hold.response == {
        "result": "RECORDED_BOOK_HOLD",
        "reason_code": "ORDER_NOT_FILLABLE",
        "market_event_id": cancelled_race_event_id,
        "order_id": order_id,
    }
    final_checkpoint = store.reconcile(
        ACCOUNT_ID,
        checkpoint_id="phase7-recorded-book-cancelled",
        created_at=datetime.now(UTC),
    )
    assert final_checkpoint.status == "HEALTHY"

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,filled_quantity,held_amount,version FROM paper_orders WHERE order_id=%s",
            (order_id,),
        ).fetchone() == ("CANCELLED", Decimal("0.00001"), Decimal(0), 3)
        assert connection.execute(
            "SELECT available,held,version FROM paper_asset_balances "
            "WHERE account_id=%s AND asset='USDT'",
            (ACCOUNT_ID,),
        ).fetchone() == (Decimal("9999.8999"), Decimal(0), 3)
        assert connection.execute(
            "SELECT available,held,version FROM paper_asset_balances "
            "WHERE account_id=%s AND asset='BTC'",
            (ACCOUNT_ID,),
        ).fetchone() == (Decimal("0.00001"), Decimal(0), 1)
        assert connection.execute(
            "SELECT count(*),sum(quantity),sum(fee_amount) FROM paper_fills WHERE order_id=%s",
            (order_id,),
        ).fetchone() == (1, Decimal("0.00001"), Decimal("0.0001"))
        assert connection.execute(
            "SELECT fill.created_at,event.occurred_at,event.source_key,lot.acquired_at "
            "FROM paper_fills fill JOIN paper_order_events event "
            "ON event.order_id=fill.order_id AND event.source_key=fill.source_key "
            "AND event.event_type='paper.order.partially-filled.v1' "
            "JOIN paper_inventory_lots lot ON lot.source_fill_id=fill.fill_id "
            "WHERE fill.order_id=%s",
            (order_id,),
        ).fetchone() == (book_at, book_at, f"{market_event_id}:ASK", book_at)
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s",
            (order_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM paper_broker_inputs WHERE source_key=%s",
            (f"{market_event_id}:ASK",),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT event_type FROM paper_order_events WHERE order_id=%s ORDER BY order_version",
            (order_id,),
        ).fetchall() == [
            ("paper.order.accepted.v2",),
            ("paper.order.partially-filled.v1",),
            ("paper.order.cancelled.v2",),
        ]
        fill_journals = connection.execute(
            "SELECT journal_kind FROM paper_ledger_transactions WHERE account_id=%s "
            "AND business_event_type='paper.fill' ORDER BY journal_kind",
            (ACCOUNT_ID,),
        ).fetchall()
        assert fill_journals == [("PHYSICAL",), ("VALUATION",)]
        assert (
            connection.execute(
                "SELECT count(*) FROM paper_reconciliation_checkpoints WHERE account_id=%s "
                "AND status='HEALTHY'",
                (ACCOUNT_ID,),
            ).fetchone()[0]
            >= 4
        )


def test_recorded_book_rejects_malformed_or_unbound_raw_provenance_without_effects(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization(
        "recorded-book-provenance", healthy_reconciliation=True
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    order_id = str(store.attempt_phase7_authorization(authorization_id).response["order_id"])
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-recorded-book-provenance-open",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )

    def raw_book(sequence: int, *, ask: str = "10000") -> bytes:
        return canonical_json(
            {
                "u": sequence,
                "s": "BTCUSDT",
                "b": "9999",
                "B": "0.00010000",
                "a": ask,
                "A": "0.00010000",
            }
        ).encode("utf-8")

    cases = (
        (
            "tampered-bytes",
            9200,
            raw_book(9200, ask="10001"),
            sha256(raw_book(9200)).hexdigest(),
            "10000",
        ),
        ("tampered-hash", 9201, raw_book(9201), "f" * 64, "10000"),
        (
            "tampered-normalized",
            9202,
            raw_book(9202),
            sha256(raw_book(9202)).hexdigest(),
            "10001",
        ),
        ("malformed-json", 9203, b"{", sha256(b"{").hexdigest(), "10000"),
    )
    event_ids: list[str] = []
    for suffix, sequence, payload_bytes, payload_hash, normalized_ask in cases:
        event_id, _ = _record_current_public_book(
            f"recorded-book-provenance-{suffix}",
            sequence=sequence,
            ask_price=normalized_ask,
            raw_payload_bytes=payload_bytes,
            raw_payload_hash=payload_hash,
        )
        event_ids.append(event_id)
        with psycopg.connect(PAPER_WRITER_URL) as connection:
            assert connection.execute(
                "SELECT paper_recorded_book_market_is_current_v1(%s)", (event_id,)
            ).fetchone() == (False,)
        held = store.apply_phase7_recorded_book(order_id, event_id)
        assert held.created is False
        assert held.response["reason_code"] == "CURRENT_MARKET_STATE_UNHEALTHY"

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_broker_inputs WHERE source_key LIKE ANY(%s)",
            ([f"{event_id}:%" for event_id in event_ids],),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_fills WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)


def test_no_fill_replay_returns_immutable_first_response_after_later_fill(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization(
        "recorded-book-no-fill-replay", healthy_reconciliation=True
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    order_id = str(store.attempt_phase7_authorization(authorization_id).response["order_id"])
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-recorded-book-no-fill-open",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )

    no_fill_event_id, _ = _record_current_public_book(
        "recorded-book-no-fill", sequence=9300, bid_price="10000", ask_price="10001"
    )
    first = store.apply_phase7_recorded_book(order_id, no_fill_event_id)
    assert first.created is True
    assert first.response == {
        "result": "OBSERVATION_APPLIED",
        "market_event_id": no_fill_event_id,
        "order_id": order_id,
        "status": "OPEN",
        "fill_id": None,
    }
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-recorded-book-no-fill-applied",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )

    fill_event_id, _ = _record_current_public_book("recorded-book-later-fill", sequence=9301)
    later = store.apply_phase7_recorded_book(order_id, fill_event_id)
    assert later.response["status"] == "PARTIALLY_FILLED"
    replay = PostgresPaperStore(PAPER_WRITER_URL).apply_phase7_recorded_book(
        order_id, no_fill_event_id
    )
    assert replay.created is False
    assert replay.response == first.response
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT effect_kind,phase7_response FROM paper_observation_effects "
            "WHERE order_id=%s AND source_key=%s",
            (order_id, f"{no_fill_event_id}:ASK"),
        ).fetchone() == ("NO_FILL", first.response)


def test_same_side_orders_compete_in_accepted_at_order_id_order_for_one_book_budget(
    postgres: None,
) -> None:
    selector_source = inspect.getsource(PostgresPaperStore.apply_next_phase7_recorded_book)
    assert '"accepted.occurred_at,orders.order_id LIMIT 1"' in selector_source
    assert "orders.accepted_broker_seq,orders.client_order_id" not in selector_source
    store = PostgresPaperStore(PAPER_WRITER_URL)
    first_authorization, _ = _seed_authorization("same-side-0-a", healthy_reconciliation=True)
    first_order_id = str(
        store.attempt_phase7_authorization(first_authorization).response["order_id"]
    )
    second_authorization, _ = _seed_authorization("same-side-0-b", healthy_reconciliation=True)
    second_order_id = str(
        store.attempt_phase7_authorization(second_authorization).response["order_id"]
    )
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-same-side-open",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )
    with psycopg.connect(DATABASE_URL) as connection:
        accepted = connection.execute(
            "SELECT event.occurred_at,event.order_id FROM paper_order_events event "
            "WHERE event.order_id=ANY(%s) AND event.order_version=1 "
            "AND event.event_type='paper.order.accepted.v2' "
            "ORDER BY event.occurred_at,event.order_id",
            ([first_order_id, second_order_id],),
        ).fetchall()
    assert len(accepted) == 2

    market_event_id, _ = _record_current_public_book("same-side-budget", sequence=9400)
    first_effect = DurableRecordedBookWorker(store).run_once()
    assert first_effect is not None
    assert first_effect.response["order_id"] == accepted[0][1]
    assert first_effect.response["status"] == "PARTIALLY_FILLED"
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-same-side-first-effect",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )
    second_effect = DurableRecordedBookWorker(store).run_once()
    assert second_effect is not None
    assert second_effect.response == {
        "result": "OBSERVATION_APPLIED",
        "market_event_id": market_event_id,
        "order_id": accepted[1][1],
        "status": "OPEN",
        "fill_id": None,
    }
    assert DurableRecordedBookWorker(store).run_once() is None
    with psycopg.connect(DATABASE_URL) as connection:
        source_key = f"{market_event_id}:ASK"
        assert connection.execute(
            "SELECT order_id,effect_kind FROM paper_observation_effects "
            "WHERE source_key=%s ORDER BY order_id",
            (source_key,),
        ).fetchall() == sorted([(accepted[0][1], "FILL"), (accepted[1][1], "NO_FILL")])
        assert connection.execute(
            "SELECT available_quantity FROM paper_broker_inputs WHERE source_key=%s",
            (source_key,),
        ).fetchone() == (Decimal("0.0001"),)
        assert connection.execute(
            "SELECT sum(quantity) FROM paper_fills WHERE source_key=%s", (source_key,)
        ).fetchone() == (Decimal("0.00001"),)


def test_recorded_book_revalidates_current_stream_health_and_defers_stale_event(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization(
        "recorded-book-current-health", healthy_reconciliation=True
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    created = store.attempt_phase7_authorization(authorization_id)
    order_id = str(created.response["order_id"])
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-recorded-book-health-open",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )

    invalid_history_event_id, _ = _record_current_public_book(
        "recorded-book-nonempty-normalized-reasons",
        sequence=9100,
        normalized_quality_reasons=("sequence_gap",),
    )
    valid_event_id, _ = _record_current_public_book(
        "recorded-book-current-health-valid", sequence=9101
    )
    worker = DurableRecordedBookWorker(store)

    def recorded_book_is_current(event_id: str) -> tuple[bool]:
        with psycopg.connect(PAPER_WRITER_URL) as paper_connection:
            row = paper_connection.execute(
                "SELECT paper_recorded_book_market_is_current_v1(%s)", (event_id,)
            ).fetchone()
            assert row is not None
            return row

    with psycopg.connect(PAPER_WRITER_URL) as paper_connection:
        assert paper_connection.execute(
            "SELECT paper_recorded_book_market_is_current_v1(%s)",
            (invalid_history_event_id,),
        ).fetchone() == (False,)
        assert paper_connection.execute(
            "SELECT paper_recorded_book_market_is_current_v1(%s)", (valid_event_id,)
        ).fetchone() == (True,)

    freshness_blocker = psycopg.connect(DATABASE_URL)
    try:
        freshness_blocker.execute(
            "SELECT 1 FROM stream_watermark_projections "
            "WHERE collector_session_id='00000000-0000-7000-8000-000000000777' "
            "FOR UPDATE"
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            delayed_validation = executor.submit(recorded_book_is_current, valid_event_id)
            freshness_blocker.execute("SELECT pg_sleep(5.2)")
            freshness_blocker.commit()
            assert delayed_validation.result(timeout=5) == (False,)
    finally:
        freshness_blocker.close()
    valid_event_id, _ = _record_current_public_book(
        "recorded-book-current-health-refreshed", sequence=9102
    )

    session_inserter = psycopg.connect(DATABASE_URL)
    try:
        session_inserter.execute(
            "INSERT INTO collector_sessions"
            "(id,source,connection_id,allowlist_version,status,started_at,ended_at) "
            "VALUES ('00000000-0000-7000-8000-000000000779','binance_spot_public',"
            "'phase7-concurrent-session',"
            "'binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5',"
            "'degraded',clock_timestamp(),NULL)"
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            concurrent_poll = executor.submit(worker.run_once)
            assert concurrent_poll.result(timeout=5) is None
    finally:
        session_inserter.rollback()
        session_inserter.close()
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "INSERT INTO collector_sessions"
            "(id,source,connection_id,allowlist_version,status,started_at,ended_at) "
            "VALUES ('00000000-0000-7000-8000-000000000778','binance_spot_public',"
            "'phase7-newer-session',"
            "'binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5',"
            "'degraded',clock_timestamp(),NULL)"
        )
    with psycopg.connect(PAPER_WRITER_URL) as paper_connection:
        assert paper_connection.execute(
            "SELECT paper_recorded_book_market_is_current_v1(%s)", (valid_event_id,)
        ).fetchone() == (False,)
    assert worker.run_once() is None
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)
        connection.execute(
            "UPDATE collector_sessions SET ended_at=clock_timestamp() "
            "WHERE id='00000000-0000-7000-8000-000000000778'"
        )

    quality_writer = psycopg.connect(DATABASE_URL)
    validation_started = Event()

    def validate_during_quality_transition() -> tuple[bool]:
        validation_started.set()
        return recorded_book_is_current(valid_event_id)

    try:
        quality_writer.execute(
            "UPDATE stream_watermark_projections SET quality_status='stale' "
            "WHERE collector_session_id='00000000-0000-7000-8000-000000000777'"
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            validation = executor.submit(validate_during_quality_transition)
            assert validation_started.wait(timeout=5)
            quality_writer.execute("SELECT pg_sleep(0.1)")
            assert not validation.done()
            quality_writer.execute(
                "UPDATE market_status_projections SET quality_status='healthy',"
                "quality_reasons='[\"sequence_gap\"]'::jsonb WHERE symbol='BTCUSDT'"
            )
            quality_writer.execute(
                "UPDATE collector_sessions SET status='reconnecting' "
                "WHERE id='00000000-0000-7000-8000-000000000777'"
            )
            quality_writer.commit()
            assert validation.result(timeout=5) == (False,)
    finally:
        quality_writer.close()
    assert worker.run_once() is None
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_fills WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)
        connection.execute(
            "UPDATE collector_sessions SET status='healthy' "
            "WHERE id='00000000-0000-7000-8000-000000000777'"
        )
        connection.execute(
            "UPDATE stream_watermark_projections SET quality_status='healthy' "
            "WHERE collector_session_id='00000000-0000-7000-8000-000000000777'"
        )

    with psycopg.connect(PAPER_WRITER_URL) as paper_connection:
        assert paper_connection.execute(
            "SELECT paper_recorded_book_market_is_current_v1(%s)", (valid_event_id,)
        ).fetchone() == (False,)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "UPDATE market_status_projections SET quality_reasons='[]'::jsonb "
            "WHERE symbol='BTCUSDT'"
        )
    with psycopg.connect(PAPER_WRITER_URL) as paper_connection:
        assert paper_connection.execute(
            "SELECT paper_recorded_book_market_is_current_v1(%s)", (valid_event_id,)
        ).fetchone() == (True,)

    applied = worker.run_once()
    assert applied is not None
    assert applied.response["result"] == "OBSERVATION_APPLIED"
    assert applied.response["market_event_id"] == valid_event_id


def test_recorded_book_kill_barrier_holds_without_any_paper_effect(postgres: None) -> None:
    authorization_id, _ = _seed_authorization(
        "recorded-book-kill-hold", healthy_reconciliation=True
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    created = store.attempt_phase7_authorization(authorization_id)
    order_id = str(created.response["order_id"])
    assert (
        store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-recorded-book-kill-open",
            created_at=datetime.now(UTC),
        ).status
        == "HEALTHY"
    )
    market_event_id, _ = _record_current_public_book("recorded-book-kill-hold", sequence=9200)
    PostgresKillSwitch(DATABASE_URL).activate(
        KillActivation(
            request_id="phase7-recorded-book-kill-hold",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="operator:phase7-recorded-book",
            reason_code="MANUAL_SAFETY_STOP",
            reason="prove recorded books cannot bypass the Kill barrier",
            observed_at=datetime.now(UTC),
            context_digest=canonical_hash({"recorded-book-kill": order_id}),
        )
    )

    held = store.apply_phase7_recorded_book(order_id, market_event_id)
    assert held.response == {
        "result": "RECORDED_BOOK_HOLD",
        "reason_code": "PAPER_KILL_SWITCH_ACTIVE",
        "market_event_id": market_event_id,
        "order_id": order_id,
    }
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,filled_quantity,version FROM paper_orders WHERE order_id=%s",
            (order_id,),
        ).fetchone() == ("OPEN", Decimal(0), 1)
        assert connection.execute(
            "SELECT count(*) FROM paper_observation_effects WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_fills WHERE order_id=%s", (order_id,)
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("changed_symbol", "changed_bid", "changed_ask"),
    [("BTCUSDT", "10001", "10002"), ("ETHUSDT", "2999", "3001")],
)
def test_newer_changed_book_blocks_and_audit_projection_removes_nonce(
    postgres: None, changed_symbol: str, changed_bid: str, changed_ask: str
) -> None:
    suffix = f"book-drift-audit-{changed_symbol.lower()}"
    authorization_id, _ = _seed_authorization(suffix, healthy_reconciliation=True)
    observed_at = datetime.now(UTC)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO normalized_market_events"
            "(id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,"
            "sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,"
            "stream_watermark,payload) VALUES (%s,%s,'book_ticker',"
            "'woozoo.market-event/v1','binance_spot_public',%s,%s,%s,99,%s,"
            "'phase7-book-drift','healthy','[]'::jsonb,%s,%s)",
            (
                canonical_hash({"newer-book": authorization_id}),
                canonical_hash({"newer-raw": authorization_id}),
                changed_symbol,
                observed_at,
                observed_at,
                canonical_hash({"newer-raw-hash": authorization_id}),
                Jsonb({"last_sequence": 99}),
                Jsonb({"bid_price": changed_bid, "ask_price": changed_ask}),
            ),
        )
        connection.execute("SET session_replication_role='origin'")

    result = PostgresPaperStore(PAPER_WRITER_URL).attempt_phase7_authorization(authorization_id)
    assert result.response["reason_code"] == "HASH_MISMATCH"
    _assert_blocked_only(authorization_id, "HASH_MISMATCH")
    with psycopg.connect(DATABASE_URL) as connection:
        raw, projected = connection.execute(
            "SELECT event.payload::text,audit.data::text FROM outbox_events event "
            "JOIN trading_room_audit_reader_v1 audit USING(event_id) "
            "WHERE event.event_type='paper.authorization.blocked.v2'"
        ).fetchone()
        assert "authorization_nonce" in raw
        assert "authorization_nonce" not in projected
        assert connection.execute(
            "SELECT producer,actor_id FROM trading_room_audit_reader_v1 "
            "WHERE event_type='paper.authorization.blocked.v2'"
        ).fetchone() == ("paper-engine", None)


def test_six_second_old_books_terminally_block_first_attempt(postgres: None) -> None:
    authorization_id, _ = _seed_authorization(
        "six-second-stale-books", healthy_reconciliation=True, book_age_seconds=6
    )
    result = PostgresPaperStore(PAPER_WRITER_URL).attempt_phase7_authorization(authorization_id)
    assert result.response["reason_code"] == "DATA_STALE"
    _assert_blocked_only(authorization_id, "DATA_STALE")


def test_non_target_same_midpoint_book_drift_terminally_blocks_first_attempt(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization(
        "same-midpoint-non-target-drift", healthy_reconciliation=True
    )
    observed_at = datetime.now(UTC)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO normalized_market_events"
            "(id,raw_event_id,event_type,schema_version,source,symbol,event_time,received_at,"
            "sequence,raw_payload_hash,correlation_id,quality_status,quality_reasons,"
            "stream_watermark,payload) VALUES (%s,%s,'book_ticker',"
            "'woozoo.market-event/v1','binance_spot_public','ETHUSDT',%s,%s,100,%s,"
            "'phase7-same-midpoint-drift','healthy','[]'::jsonb,%s,%s)",
            (
                canonical_hash({"same-midpoint-book": authorization_id}),
                canonical_hash({"same-midpoint-raw": authorization_id}),
                observed_at,
                observed_at,
                canonical_hash({"same-midpoint-raw-hash": authorization_id}),
                Jsonb({"last_sequence": 100}),
                Jsonb({"bid_price": "1998", "ask_price": "2002"}),
            ),
        )
        connection.execute("SET session_replication_role='origin'")

    result = PostgresPaperStore(PAPER_WRITER_URL).attempt_phase7_authorization(authorization_id)
    assert result.response["reason_code"] == "HASH_MISMATCH"
    _assert_blocked_only(authorization_id, "HASH_MISMATCH")


def test_reconciliation_serializes_on_the_paper_account_authority_lock(postgres: None) -> None:
    store = PostgresPaperStore(PAPER_WRITER_URL)
    expected_digest = store.semantic_digest(ACCOUNT_ID)
    ready = Event()

    def reconcile() -> object:
        ready.set()
        return store.reconcile(
            ACCOUNT_ID,
            checkpoint_id="phase7-reconciliation-lock",
            created_at=datetime.now(UTC),
        )

    blocker = psycopg.connect(DATABASE_URL)
    blocker.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"paper-account:{ACCOUNT_ID}",),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(reconcile)
            assert ready.wait(timeout=5)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.25)
            blocker.commit()
            result = future.result(timeout=10)
    finally:
        blocker.close()

    assert getattr(result, "status") == "HEALTHY"
    assert getattr(result, "input_digest") == expected_digest


def test_revocation_and_first_attempt_serialize_without_revoked_order(postgres: None) -> None:
    authorization_id, _ = _seed_authorization("revocation-race", healthy_reconciliation=True)
    observed_at = datetime.now(UTC)
    session_digest, csrf_digest = _seed_operator_csrf("revocation-race", observed_at)
    with psycopg.connect(DATABASE_URL) as connection:
        approval_id = connection.execute(
            "SELECT approval_id FROM paper_execution_authorizations WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone()[0]

    blocker = psycopg.connect(DATABASE_URL)
    blocker.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"paper-approval:{approval_id}",),
    )
    blocker.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"paper-authorization:{authorization_id}",),
    )
    attempt_ready = Event()
    revoke_ready = Event()

    def attempt() -> tuple[str, object]:
        attempt_ready.set()
        try:
            return (
                "ok",
                PostgresPaperStore(PAPER_WRITER_URL).attempt_phase7_authorization(authorization_id),
            )
        except Exception as error:  # pragma: no cover - asserted through the tagged result
            return ("error", error)

    def revoke() -> tuple[str, object]:
        revoke_ready.set()
        try:
            with psycopg.connect(DATABASE_URL) as connection:
                row = connection.execute(
                    "SELECT created,response FROM revoke_paper_approval_v1("
                    "%s,%s,%s,1,%s,'operator-local-1',%s,%s,%s,%s,%s)",
                    (
                        "phase7-revocation-race",
                        canonical_hash({"revoke": authorization_id}),
                        approval_id,
                        "race serialization proof",
                        session_digest,
                        csrf_digest,
                        canonical_hash({"origin": "revocation-race"}),
                        "revocation-nonce-race-0000000000000000",
                        observed_at,
                    ),
                ).fetchone()
            return ("ok", row)
        except Exception as error:  # pragma: no cover - asserted through the tagged result
            return ("error", error)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            attempt_future = executor.submit(attempt)
            revoke_future = executor.submit(revoke)
            assert attempt_ready.wait(timeout=5) and revoke_ready.wait(timeout=5)
            blocker.commit()
            attempt_result = attempt_future.result(timeout=10)
            revoke_result = revoke_future.result(timeout=10)
    finally:
        blocker.close()

    with psycopg.connect(DATABASE_URL) as connection:
        revoked = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM paper_approval_revocations WHERE approval_id=%s)",
            (approval_id,),
        ).fetchone()[0]
        order_count = connection.execute(
            "SELECT count(*) FROM paper_orders WHERE authorization_id=%s", (authorization_id,)
        ).fetchone()[0]
        attempt_row = connection.execute(
            "SELECT outcome,reason_code FROM paper_authorization_attempts "
            "WHERE authorization_id=%s",
            (authorization_id,),
        ).fetchone()
    assert not (revoked and order_count)
    if revoked:
        assert revoke_result[0] == "ok"
        assert attempt_row == ("BLOCKED", "AUTHORIZATION_REVOKED")
    else:
        assert attempt_result[0] == "ok"
        assert order_count == 1
        assert revoke_result[0] == "error"
        assert "APPROVAL_NOT_REVOCABLE" in str(revoke_result[1])


def test_recovery_races_consumer_but_requires_completion_and_later_checkpoint(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization("recovery-race", healthy_reconciliation=True)
    store = PostgresPaperStore(PAPER_WRITER_URL)
    created = store.attempt_phase7_authorization(authorization_id)
    assert created.response["result"] == "CONSUMED_ORDER_CREATED"
    activated = PostgresKillSwitch(DATABASE_URL).activate(
        KillActivation(
            request_id="phase7-recovery-race",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="operator:phase7-recovery",
            reason_code="MANUAL_SAFETY_STOP",
            reason="race recovery against cancellation consumer",
            observed_at=datetime.now(UTC),
            context_digest=canonical_hash({"recovery-race": authorization_id}),
        )
    )
    with psycopg.connect(DATABASE_URL) as connection:
        payload_hash = connection.execute(
            "SELECT payload_hash FROM outbox_events WHERE event_id=%s",
            (activated.outbox_event_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO paper_authorization_worker_state"
            "(worker_name,instance_id,status,started_at,heartbeat_at) VALUES "
            "('phase7-paper-authorization','recovery-race-worker','RUNNING',%s,%s) "
            "ON CONFLICT (worker_name) DO UPDATE SET "
            "instance_id=EXCLUDED.instance_id,status='RUNNING',"
            "started_at=EXCLUDED.started_at,heartbeat_at=EXCLUDED.heartbeat_at,"
            "last_progress_at=NULL,last_result=NULL,last_error_code=NULL,stopped_at=NULL",
            (datetime.now(UTC), datetime.now(UTC)),
        )
    observed_at = datetime.now(UTC)
    session_digest, csrf_digest = _seed_operator_csrf("recovery-race", observed_at)
    recovery_key = "phase7-recovery-race-command"
    recovery_hash = canonical_hash({"recovery": activated.activation_event_id})
    _record_current_public_books("recovery-race-initial", sequence=9600)
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT cancellation_status,open_order_count,data_status,"
            "reconciliation_status,ledger_status,recovery_allowed "
            "FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("INCOMPLETE", 1, "HEALTHY", "UNHEALTHY", "BALANCED", False)

    def recover() -> tuple[str, object]:
        recovery_ready.set()
        try:
            with psycopg.connect(DATABASE_URL) as connection:
                row = connection.execute(
                    "SELECT created,response FROM recover_kill_switch_v1("
                    "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
                    (
                        recovery_key,
                        recovery_hash,
                        activated.activation_event_id,
                        "INCIDENT-P7-RACE",
                        "cancellation and reconciliation reviewed",
                        session_digest,
                        csrf_digest,
                        canonical_hash({"origin": "recovery-race"}),
                        observed_at,
                    ),
                ).fetchone()
            return ("ok", row)
        except Exception as error:  # pragma: no cover - asserted through the tagged result
            return ("error", error)

    def consume() -> object:
        consumer_ready.set()
        return store.consume_kill_activation(
            activated.activation_event_id, payload_hash, received_at=observed_at
        )

    blocker = psycopg.connect(DATABASE_URL)
    blocker.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"paper-account:{ACCOUNT_ID}",),
    )
    recovery_ready = Event()
    consumer_ready = Event()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            recovery_future = executor.submit(recover)
            consumer_future = executor.submit(consume)
            assert recovery_ready.wait(timeout=5) and consumer_ready.wait(timeout=5)
            blocker.commit()
            recovery_result = recovery_future.result(timeout=10)
            consumer_result = consumer_future.result(timeout=10)
    finally:
        blocker.close()

    assert recovery_result[0] == "error"
    assert getattr(consumer_result, "completion_created") is True
    _record_current_public_books("recovery-race-completed", sequence=9601)
    with psycopg.connect(DATABASE_URL) as connection:
        completion = connection.execute(
            "SELECT state_digest,completed_at FROM paper_kill_cancel_completions "
            "WHERE activation_event_id=%s",
            (activated.activation_event_id,),
        ).fetchone()
        assert completion is not None
        assert connection.execute(
            "SELECT count(*) FROM paper_orders WHERE status IN ('OPEN','PARTIALLY_FILLED')"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT cancellation_status,open_order_count,data_status,"
            "reconciliation_status,ledger_status,recovery_allowed "
            "FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("COMPLETE", 0, "HEALTHY", "UNHEALTHY", "BALANCED", False)

    checkpoint_time = datetime.now(UTC)
    checkpoint = store.reconcile(
        ACCOUNT_ID,
        checkpoint_id="phase7-post-kill-completion",
        created_at=checkpoint_time,
    )
    assert checkpoint.status == "HEALTHY" and checkpoint.input_digest == completion[0]
    _record_current_public_books("recovery-race-ready", sequence=9602)
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT cancellation_status,open_order_count,data_status,"
            "reconciliation_status,ledger_status,recovery_allowed "
            "FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("COMPLETE", 0, "HEALTHY", "HEALTHY", "BALANCED", True)

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "UPDATE market_status_projections SET quality_status='stale',"
            "quality_reasons='[\"sequence_gap\"]'::jsonb WHERE symbol='BTCUSDT'"
        )
        connection.execute(
            "UPDATE stream_watermark_projections SET quality_status='stale' "
            "WHERE collector_session_id='00000000-0000-7000-8000-000000000777' "
            "AND stream='btcusdt@bookTicker'"
        )
        connection.execute(
            "UPDATE collector_sessions SET status='stale' "
            "WHERE id='00000000-0000-7000-8000-000000000777'"
        )
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT data_status,recovery_allowed FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("UNHEALTHY", False)
        with pytest.raises(psycopg.errors.RaiseException, match="KILL_RECOVERY_DATA_UNHEALTHY"):
            connection.execute(
                "SELECT created,response FROM recover_kill_switch_v1("
                "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
                (
                    recovery_key,
                    recovery_hash,
                    activated.activation_event_id,
                    "INCIDENT-P7-RACE",
                    "cancellation and reconciliation reviewed",
                    session_digest,
                    csrf_digest,
                    canonical_hash({"origin": "recovery-race"}),
                    datetime.now(UTC),
                ),
            )
        connection.rollback()
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (True,)
        assert connection.execute(
            "SELECT count(*) FROM risk_kill_recovery_command_receipts WHERE idempotency_key=%s",
            (recovery_key,),
        ).fetchone() == (0,)

    _record_current_public_books("recovery-race-repaired", sequence=9603)
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT data_status,recovery_allowed FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("HEALTHY", True)
        recovered = connection.execute(
            "SELECT created,response FROM recover_kill_switch_v1("
            "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
            (
                recovery_key,
                recovery_hash,
                activated.activation_event_id,
                "INCIDENT-P7-RACE",
                "cancellation and reconciliation reviewed",
                session_digest,
                csrf_digest,
                canonical_hash({"origin": "recovery-race"}),
                datetime.now(UTC),
            ),
        ).fetchone()
    assert recovered is not None and recovered[0] is True
    assert recovered[1]["result"] == "RECOVERED"
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,cancellation_status,recovery_allowed FROM kill_switch_recovery_reader_v1"
        ).fetchone() == (False, "NOT_ACTIVE", False)


def test_kill_completion_waits_for_pending_authorization_and_recovery_resumes(
    postgres: None,
) -> None:
    pending_authorization_id, _ = _seed_authorization(
        "recovery-authority-drift-pending", healthy_reconciliation=False
    )
    open_authorization_id, _ = _seed_authorization(
        "recovery-authority-drift-open", healthy_reconciliation=True
    )
    store = PostgresPaperStore(PAPER_WRITER_URL)
    created = store.attempt_phase7_authorization(open_authorization_id)
    assert created.response["result"] == "CONSUMED_ORDER_CREATED"

    activated = PostgresKillSwitch(DATABASE_URL).activate(
        KillActivation(
            request_id="phase7-recovery-authority-drift",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="operator:phase7-recovery",
            reason_code="MANUAL_SAFETY_STOP",
            reason="prove recovery rejects a stale authority checkpoint",
            observed_at=datetime.now(UTC),
            context_digest=canonical_hash({"recovery-drift": open_authorization_id}),
        )
    )
    with psycopg.connect(DATABASE_URL) as connection:
        payload_hash = connection.execute(
            "SELECT payload_hash FROM outbox_events WHERE event_id=%s",
            (activated.outbox_event_id,),
        ).fetchone()[0]
    cancellation_waiting_for_drain = store.consume_kill_activation(
        activated.activation_event_id,
        payload_hash,
        received_at=datetime.now(UTC),
    )
    assert cancellation_waiting_for_drain.cancelled_count == 1
    assert cancellation_waiting_for_drain.has_more is True
    assert cancellation_waiting_for_drain.completion_created is False
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_kill_cancel_completions WHERE activation_event_id=%s",
            (activated.activation_event_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT authorization_id FROM paper_pending_authorizations_v1"
        ).fetchall() == [(pending_authorization_id,)]
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="PAPER_KILL_CANCELLATION_NOT_COMPLETE",
    ):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO paper_kill_cancel_completions("
                "activation_event_id,payload_hash,paper_account_id,batch_count,"
                "cancelled_count,state_digest,completed_at) VALUES (%s,%s,%s,1,1,%s,%s)",
                (
                    activated.activation_event_id,
                    payload_hash,
                    ACCOUNT_ID,
                    store.semantic_digest(ACCOUNT_ID),
                    datetime.now(UTC),
                ),
            )
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM paper_kill_cancel_completions WHERE activation_event_id=%s",
            (activated.activation_event_id,),
        ).fetchone() == (0,)
    pending_effect = store.attempt_phase7_authorization(pending_authorization_id)
    assert pending_effect.created is True
    assert pending_effect.response == {
        "result": "BLOCKED",
        "authorization_id": pending_authorization_id,
        "reason_code": "KILL_SWITCH_ACTIVE",
    }

    with psycopg.connect(DATABASE_URL) as connection:
        completion = connection.execute(
            "SELECT state_digest,completed_at FROM paper_kill_cancel_completions "
            "WHERE activation_event_id=%s",
            (activated.activation_event_id,),
        ).fetchone()
        assert completion is not None
        assert connection.execute(
            "SELECT count(*) FROM paper_pending_authorizations_v1"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM paper_pending_kill_activations_v1"
        ).fetchone() == (0,)
    assert pending_effect.semantic_digest == completion[0]

    observed_at = datetime.now(UTC)
    session_digest, csrf_digest = _seed_operator_csrf("recovery-authority-drift", observed_at)
    with psycopg.connect(DATABASE_URL) as connection:
        blocked_link_count = connection.execute(
            "SELECT count(*) FROM paper_outbox_links link "
            "JOIN outbox_events event USING(event_id) "
            "WHERE link.account_id=%s AND event.event_type='paper.authorization.blocked.v2' "
            "AND event.aggregate_id=%s",
            (ACCOUNT_ID, pending_authorization_id),
        ).fetchone()
        connection.execute(
            "INSERT INTO paper_authorization_worker_state"
            "(worker_name,instance_id,status,started_at,heartbeat_at) VALUES "
            "('phase7-paper-authorization','recovery-authority-drift-worker','RUNNING',%s,%s) "
            "ON CONFLICT (worker_name) DO UPDATE SET "
            "instance_id=EXCLUDED.instance_id,status='RUNNING',"
            "started_at=EXCLUDED.started_at,heartbeat_at=EXCLUDED.heartbeat_at,"
            "last_progress_at=NULL,last_result=NULL,last_error_code=NULL,stopped_at=NULL",
            (observed_at, observed_at),
        )
    assert blocked_link_count == (1,)
    _record_current_public_books("recovery-authority-drift", sequence=9700)
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT cancellation_status,open_order_count,data_status,"
            "reconciliation_status,ledger_status,recovery_allowed "
            "FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("COMPLETE", 0, "HEALTHY", "UNHEALTHY", "BALANCED", False)

    recovery_key = "phase7-recovery-authority-drift-command"
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="KILL_RECOVERY_RECONCILIATION_UNHEALTHY",
    ):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT created,response FROM recover_kill_switch_v1("
                "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
                (
                    recovery_key,
                    canonical_hash({"recovery": activated.activation_event_id}),
                    activated.activation_event_id,
                    "INCIDENT-P7-AUTHORITY-DRIFT",
                    "pending authorization effect requires reconciliation",
                    session_digest,
                    csrf_digest,
                    canonical_hash({"origin": "recovery-authority-drift"}),
                    datetime.now(UTC),
                ),
            )
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (True, 1)
        assert connection.execute(
            "SELECT count(*) FROM risk_kill_recovery_command_receipts WHERE idempotency_key=%s",
            (recovery_key,),
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM kill_recovery_events").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM outbox_events WHERE event_type='kill-switch.recovered.v2'"
        ).fetchone() == (0,)

    checkpoint_after_completion = store.reconcile(
        ACCOUNT_ID,
        checkpoint_id="phase7-recovery-authority-complete",
        created_at=datetime.now(UTC),
    )
    assert checkpoint_after_completion.status == "HEALTHY"
    assert checkpoint_after_completion.input_digest == completion[0]
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT cancellation_status,open_order_count,data_status,"
            "reconciliation_status,ledger_status,recovery_allowed "
            "FROM kill_switch_recovery_reader_v1"
        ).fetchone() == ("COMPLETE", 0, "HEALTHY", "HEALTHY", "BALANCED", True)
        recovered = connection.execute(
            "SELECT created,response FROM recover_kill_switch_v1("
            "%s,%s,1,%s,%s,%s,'operator-local-1',%s,%s,%s,%s)",
            (
                recovery_key,
                canonical_hash({"recovery": activated.activation_event_id}),
                activated.activation_event_id,
                "INCIDENT-P7-AUTHORITY-DRIFT",
                "pending authorization drained before immutable completion",
                session_digest,
                csrf_digest,
                canonical_hash({"origin": "recovery-authority-drift"}),
                datetime.now(UTC),
            ),
        ).fetchone()
    assert recovered is not None and recovered[0] is True
    assert recovered[1]["result"] == "RECOVERED"
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (False, 2)
        assert connection.execute(
            "SELECT count(*) FROM risk_kill_recovery_command_receipts WHERE idempotency_key=%s",
            (recovery_key,),
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM kill_recovery_events").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM outbox_events WHERE event_type='kill-switch.recovered.v2'"
        ).fetchone() == (1,)


def test_test_namespace_attempt_cannot_satisfy_pending_paper_authorization(
    postgres: None,
) -> None:
    authorization_id, _ = _seed_authorization(
        "kill-pending-namespace-collision", healthy_reconciliation=False
    )
    observed_at = datetime.now(UTC)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO paper_authorization_attempts("
            "authorization_id,authorization_nonce,account_id,command_scope,"
            "idempotency_key,namespace,request_hash,outcome,reason_code,created_at) "
            "VALUES (%s,%s,%s,'paper.create.v1',%s,'test',%s,'BLOCKED','TEST_BLOCK',%s)",
            (
                authorization_id,
                "test-namespace-collision-nonce",
                ACCOUNT_ID,
                "test-namespace-collision-key",
                canonical_hash({"test-namespace-collision": authorization_id}),
                observed_at,
            ),
        )
        connection.execute("SET session_replication_role='origin'")

    activated = PostgresKillSwitch(DATABASE_URL).activate(
        KillActivation(
            request_id="phase7-kill-pending-namespace-collision",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="operator:phase7-namespace-collision",
            reason_code="MANUAL_SAFETY_STOP",
            reason="prove test attempts cannot drain paper authorizations",
            observed_at=observed_at,
            context_digest=canonical_hash({"namespace-collision": authorization_id}),
        )
    )
    with psycopg.connect(DATABASE_URL) as connection:
        payload_hash = connection.execute(
            "SELECT payload_hash FROM outbox_events WHERE event_id=%s",
            (activated.outbox_event_id,),
        ).fetchone()[0]

    waiting = PostgresPaperStore(PAPER_WRITER_URL).consume_kill_activation(
        activated.activation_event_id,
        payload_hash,
        received_at=datetime.now(UTC),
    )
    assert waiting.cancelled_count == 0
    assert waiting.has_more is True
    assert waiting.completion_created is False
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT authorization_id FROM paper_pending_authorizations_v1"
        ).fetchall() == [(authorization_id,)]
        assert connection.execute(
            "SELECT count(*) FROM paper_kill_cancel_completions WHERE activation_event_id=%s",
            (activated.activation_event_id,),
        ).fetchone() == (0,)
