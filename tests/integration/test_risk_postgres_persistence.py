from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb
import pytest

from risk_engine import (
    KillActivation,
    PostgresKillSwitch,
    PostgresRiskStore,
    RiskPersistenceStage,
    canonical_hash,
    evaluate_risk,
)


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
RISK_WRITER_URL = "postgresql://woozoo_risk_engine@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
NOW = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)


def risk_input() -> dict[str, object]:
    proposal_payload = {
        "schema_version": "woozoo.trade-proposal/v1",
        "proposal_id": "fixture-proposal-persistence",
        "symbol": "BTCUSDT",
        "side": "BUY",
    }
    portfolio = {
        "snapshot_id": "portfolio-persistence",
        "available_quote": "10000",
        "held_quote": "0",
        "fee_liabilities": "0",
        "positions": {
            "BTCUSDT": {"available": "0", "held": "0", "midpoint": "100"},
            "ETHUSDT": {"available": "0", "held": "0", "midpoint": "50"},
        },
        "fifo_lots": [],
        "realized_pnl_24h": "0",
        "high_water_equity": "10000",
        "open_orders": [],
    }
    preview = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "quantity": "0.1",
        "limit_price": "100",
        "worst_case_fee": "0.01",
        "worst_case_hold": "10.01",
        "worst_case_notional": "10.01",
        "best_bid": "99.99",
        "best_ask": "100",
        "expected_slippage_inputs": {"method": "limit-vs-book-v1"},
    }
    policy = {
        "version": "woozoo.risk-policy/v1",
        "calculators": {
            "decimal": {"name": "numeric-38-18", "version": "1", "hash": "1" * 64},
            "fee": {"name": "quote-fee", "version": "1", "hash": "2" * 64},
            "valuation": {"name": "midpoint", "version": "1", "hash": "3" * 64},
            "spread": {"name": "midpoint-spread-bps", "version": "1", "hash": "4" * 64},
            "slippage": {"name": "limit-vs-book-bps", "version": "1", "hash": "5" * 64},
        },
        "limits": {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "order_types": ["LIMIT"],
            "sides": ["BUY", "SELL"],
            "time_in_force": ["GTC"],
            "max_order_notional_ratio": "0.0025",
            "symbol_exposure_ratios": {"BTCUSDT": "0.15", "ETHUSDT": "0.10"},
            "max_portfolio_exposure_ratio": "0.25",
            "realized_loss_ratio": "0.01",
            "drawdown_ratio": "0.05",
            "max_spread_bps": "25",
            "max_expected_slippage_bps": "25",
            "duplicate_window_seconds": 900,
        },
    }
    exposure = {"snapshot_id": "exposure-persistence", "open_order_count": 0}
    reconciliation = {
        "checkpoint_id": "recon-persistence",
        "health": "HEALTHY",
        "mismatch_codes": [],
    }
    return {
        "risk_input_schema_version": "woozoo.risk-input/v1",
        "namespace": "test",
        "proposal": {
            "fixture_contract": "p6-trade-proposal-consumer/v1",
            "schema_version": "woozoo.trade-proposal/v1",
            "payload": proposal_payload,
            "proposal_hash": canonical_hash(proposal_payload),
        },
        "portfolio": {**portfolio, "snapshot_hash": canonical_hash(portfolio)},
        "data": {
            "evidence_id": "evidence-persistence",
            "evidence_hash": "a" * 64,
            "as_of": "2026-07-19T00:00:00Z",
            "knowledge_cutoff": "2026-07-19T00:00:00Z",
            "freshness": "FRESH",
            "quality": "HEALTHY",
            "future_contamination": False,
            "watermark_complete": True,
        },
        "order_preview": {**preview, "paper_order_preview_hash": canonical_hash(preview)},
        "policy": {**policy, "policy_hash": canonical_hash(policy)},
        "kill_switch": {"active": False, "version": 0, "event_id": None},
        "reconciliation": {
            **reconciliation,
            "checkpoint_hash": canonical_hash(reconciliation),
        },
        "decision_clock": {
            "decision_as_of": "2026-07-19T00:00:00Z",
            "source": "fixture-clock",
            "version": "1",
        },
        "duplicate": {
            "key": "BTCUSDT:BUY",
            "window_seconds": 900,
            "proposal_seen": False,
            "order_intent_seen": False,
        },
        "exposure_snapshot": {**exposure, "snapshot_hash": canonical_hash(exposure)},
    }


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


@contextmanager
def infrastructure_lock() -> Iterator[None]:
    path = ROOT / ".p1-integration.lock"
    with path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("could not acquire infrastructure lock")
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@pytest.fixture(scope="module", autouse=True)
def postgres() -> Iterator[None]:
    with infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            yield
        finally:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            run("docker", "compose", "down", "-v")


def test_risk_decision_persistence_is_atomic_unique_and_replay_stable() -> None:
    payload = risk_input()
    decision = evaluate_risk(payload)
    store = PostgresRiskStore(DATABASE_URL)

    first = store.persist_decision(payload, decision, recorded_at=NOW)
    replay = store.persist_decision(deepcopy(payload), decision, recorded_at=NOW)
    assert first.created is True
    assert replay.created is False
    assert replay.decision_id == first.decision_id
    assert replay.risk_input_digest == first.risk_input_digest
    assert replay.decision_hash == first.decision_hash
    assert replay.outbox_event_id == first.outbox_event_id

    failed_input = risk_input()
    failed_input["data"]["evidence_hash"] = "b" * 64  # type: ignore[index]
    failed_decision = evaluate_risk(failed_input)
    with pytest.raises(RuntimeError, match="INJECTED_RISK_FAILURE:decision"):
        store.persist_decision(
            failed_input,
            failed_decision,
            recorded_at=NOW,
            _fail_after=RiskPersistenceStage.DECISION,
        )

    with psycopg.connect(DATABASE_URL) as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM risk_decisions),"
            "(SELECT count(*) FROM risk_outbox_links WHERE aggregate_kind='risk-decision'),"
            "(SELECT count(*) FROM risk_decisions WHERE risk_input_digest=%s)",
            (failed_decision.risk_input_digest,),
        ).fetchone()
        assert counts == (1, 1, 0)
        with pytest.raises(psycopg.Error):
            connection.execute(
                "UPDATE risk_decisions SET verdict='DENIED' WHERE decision_id=%s",
                (first.decision_id,),
            )

    active_input = risk_input()
    active_input["kill_switch"] = {
        "active": True,
        "version": 1,
        "event_id": "c" * 64,
    }
    denied = evaluate_risk(active_input)
    forged_hash = canonical_hash(
        {
            "decision_schema_version": denied.decision_schema_version,
            "risk_input_digest": denied.risk_input_digest,
            "verdict": "ALLOWED",
            "ordered_reason_codes": ["RISK_ALLOWED"],
        }
    )
    forged = replace(
        denied,
        verdict="ALLOWED",
        ordered_reason_codes=("RISK_ALLOWED",),
        primary_reason_code="RISK_ALLOWED",
        decision_hash=forged_hash,
    )
    with pytest.raises(ValueError, match="FORGED_RISK_DECISION"):
        store.persist_decision(active_input, forged, recorded_at=NOW)


def test_risk_writer_cannot_reset_or_decrease_the_kill_barrier() -> None:
    unbound_event_id = "b" * 64
    unbound_outbox_id = "1" * 64
    unbound_request_hash = "c" * 64
    with psycopg.connect(RISK_WRITER_URL) as connection:
        connection.execute(
            "INSERT INTO kill_switch_events"
            "(activation_event_id,scope,request_id,request_hash,trigger_kind,actor_id,"
            "reason_code,reason,observed_at,context_digest,prior_version,new_version) "
            "VALUES (%s,'paper-global','complete-without-state',%s,'MANUAL',"
            "'operator:unbound-test','MANUAL_SAFETY_STOP','must rollback',%s,%s,0,1)",
            (unbound_event_id, unbound_request_hash, NOW, "a" * 64),
        )
        connection.execute(
            "INSERT INTO risk_kill_command_receipts"
            "(request_id,request_hash,activation_event_id,response,created_at) "
            "VALUES ('complete-without-state',%s,%s,%s,%s)",
            (
                unbound_request_hash,
                unbound_event_id,
                Jsonb({"activation_event_id": unbound_event_id, "version": 1}),
                NOW,
            ),
        )
        connection.execute(
            "INSERT INTO outbox_events"
            "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
            "aggregate_id,aggregate_version) VALUES "
            "(%s,'kill-switch.activated.v1',%s,%s,%s,'kill_switch',%s,1)",
            (
                unbound_outbox_id,
                Jsonb(
                    {
                        "producer": "risk-engine",
                        "data": {"activation_event_id": unbound_event_id},
                    }
                ),
                "2" * 64,
                NOW,
                unbound_event_id,
            ),
        )
        connection.execute(
            "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
            "VALUES (%s,'kill-switch',%s)",
            (unbound_outbox_id, unbound_event_id),
        )
        with pytest.raises(
            psycopg.errors.RaiseException, match="activation transaction is incomplete"
        ):
            connection.commit()
        connection.rollback()

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (False, 0)
        assert connection.execute(
            "SELECT "
            "(SELECT count(*) FROM kill_switch_events WHERE activation_event_id=%s),"
            "(SELECT count(*) FROM risk_kill_command_receipts WHERE activation_event_id=%s),"
            "(SELECT count(*) FROM risk_outbox_links WHERE event_id=%s),"
            "(SELECT count(*) FROM outbox_events WHERE event_id=%s)",
            (unbound_event_id, unbound_event_id, unbound_outbox_id, unbound_outbox_id),
        ).fetchone() == (0, 0, 0, 0)

    partial_event_id = "e" * 64
    with psycopg.connect(RISK_WRITER_URL) as connection:
        connection.execute(
            "INSERT INTO kill_switch_events"
            "(activation_event_id,scope,request_id,request_hash,trigger_kind,actor_id,"
            "reason_code,reason,observed_at,context_digest,prior_version,new_version) "
            "VALUES (%s,'paper-global','partial-activation',%s,'MANUAL',"
            "'operator:partial-test','MANUAL_SAFETY_STOP','must rollback',%s,%s,0,1)",
            (partial_event_id, "f" * 64, NOW, "a" * 64),
        )
        connection.execute(
            "UPDATE kill_switch_state SET active=true,version=1,last_activation_event_id=%s "
            "WHERE scope='paper-global'",
            (partial_event_id,),
        )
        with pytest.raises(
            psycopg.errors.RaiseException, match="activation transaction is incomplete"
        ):
            connection.commit()
        connection.rollback()

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone() == (False, 0)
        assert connection.execute(
            "SELECT count(*) FROM kill_switch_events WHERE activation_event_id=%s",
            (partial_event_id,),
        ).fetchone() == (0,)

    activation = PostgresKillSwitch(DATABASE_URL).activate(
        KillActivation(
            request_id="db-monotonic-kill",
            expected_version=0,
            trigger_kind="MANUAL",
            actor_id="operator:db-safety-test",
            reason_code="MANUAL_SAFETY_STOP",
            reason="verify direct writer reset is rejected",
            observed_at=NOW,
            context_digest="d" * 64,
        )
    )
    assert activation.version == 1

    with psycopg.connect(RISK_WRITER_URL) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="monotonic activation only"):
            connection.execute(
                "UPDATE kill_switch_state SET active=false,version=0,"
                "last_activation_event_id=NULL WHERE scope='paper-global'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="monotonic activation only"):
            connection.execute(
                "UPDATE kill_switch_state SET version=version-1 WHERE scope='paper-global'"
            )

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT active,version,last_activation_event_id FROM kill_switch_state "
            "WHERE scope='paper-global'"
        ).fetchone() == (True, 1, activation.activation_event_id)
