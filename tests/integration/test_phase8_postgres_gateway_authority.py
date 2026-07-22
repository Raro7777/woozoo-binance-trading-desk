from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from control_api.testnet_operator import PostgresTestnetOperatorRoom
from docker_infrastructure_lock import docker_infrastructure_lock
from spot_testnet_gateway.dispatch import GatewayRuntimeBinding
from spot_testnet_gateway.persistence import (
    PostgresGatewayDispatcher,
    PostgresGatewayObservationWriter,
)
from spot_testnet_gateway.transport import SanitizedTransportResult
from testnet_execution.canonical import canonical_digest
from testnet_execution.materializer import PostgresTestnetRiskMaterializer
from testnet_execution.persistence import ExecutionRuntimeBinding, PostgresIntentWorker
from testnet_execution.reconciliation import PostgresReconciliationWorker


ROOT = Path(__file__).parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
GATEWAY_URL = "postgresql://woozoo_spot_testnet_gateway@127.0.0.1:5433/woozoo"
CONTROL_URL = "postgresql://woozoo_control_api@127.0.0.1:5433/woozoo"
EXECUTION_URL = "postgresql://woozoo_testnet_execution@127.0.0.1:5433/woozoo"
ENVIRONMENT = {"TRADING_MODE": "paper", "DATABASE_URL": DATABASE_URL}
HASH = "a" * 64
CONFIGURATION_DIGEST = "b" * 64
ALLOWLIST_DIGEST = "c" * 64
GENERATION_ID = "d" * 64
ACCOUNT_ID = "e" * 64
ACTIVATION_ID = "f" * 64
LOCAL_ACCOUNT_ID = "39c5f6b40a6b89656775035440719d2c450999a45d7df4a5baa041e0985741e2"
INITIAL_GENERATION_ID = "fe52fe33409fccd9698bb6c0a42bc097bbaa90bd657ea8fda108dadabd40d875"


def _gateway_runtime() -> GatewayRuntimeBinding:
    return GatewayRuntimeBinding(
        gateway_instance_id="6" * 64,
        build_digest="7" * 64,
        configuration_digest=CONFIGURATION_DIGEST,
        allowlist_digest=ALLOWLIST_DIGEST,
    )


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


@pytest.fixture(scope="module")
def postgres() -> Iterator[None]:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            yield
        finally:
            run("docker", "compose", "down", "-v")


def test_phase8_clean_downgrade_upgrade_round_trip(postgres: None) -> None:
    del postgres
    run(sys.executable, "-m", "alembic", "downgrade", "20260720_0007")
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20260722_0008",
        )


def _command_payload(
    command_id: str, request_digest: str, client_order_id: str, now: datetime
) -> dict[str, object]:
    return {
        "schema_version": "woozoo.testnet-gateway-command/v1",
        "command_id": command_id,
        "command_type": "SUBMIT_LIMIT_ORDER",
        "effect_class": "CREATE_ORDER",
        "producer": "testnet-execution-service",
        "environment": "BINANCE_SPOT_TESTNET",
        "account_binding_id": ACCOUNT_ID,
        "account_generation": 1,
        "idempotency_key": f"submit:{command_id}",
        "request_digest": request_digest,
        "correlation_id": command_id,
        "causation_id": command_id,
        "capability_id": "SPOT_TESTNET_SUBMIT_LIMIT_GTC",
        "gateway_allowlist_digest": ALLOWLIST_DIGEST,
        "gateway_configuration_digest": CONFIGURATION_DIGEST,
        "activation_version": 1,
        "testnet_barrier_version": 1,
        "original_authorization_id": "1" * 64,
        "original_approval_id": "2" * 64,
        "original_proposal_hash": "3" * 64,
        "original_risk_decision_hash": "4" * 64,
        "client_order_id": client_order_id,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.001",
        "limit_price": "60000.00",
        "query_reason": "INITIAL_SUBMIT",
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }


def _seed_authority_and_command(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    command_id: str,
    request_digest: str,
    client_order_id: str,
    now: datetime,
) -> None:
    payload = _command_payload(command_id, request_digest, client_order_id, now)
    connection.execute("SET session_replication_role='replica'")
    if (
        connection.execute(
            "SELECT 1 FROM testnet_accounts WHERE account_binding_id=%s", (ACCOUNT_ID,)
        ).fetchone()
        is None
    ):
        connection.execute(
            "INSERT INTO testnet_accounts(account_binding_id,environment,account_label,created_at) "
            "VALUES (%s,'BINANCE_SPOT_TESTNET','오프라인 통합 테스트 계정',%s)",
            (ACCOUNT_ID, now),
        )
        connection.execute(
            "INSERT INTO testnet_account_generations(generation_id,account_binding_id,"
            "generation_number,status,opening_snapshot_digest,operator_confirmation_digest,"
            "opened_at,closed_at,version) VALUES (%s,%s,1,'ACTIVE',%s,NULL,%s,NULL,1)",
            (GENERATION_ID, ACCOUNT_ID, "5" * 64, now),
        )
        connection.execute(
            "UPDATE testnet_safety_state SET active=false,version=1,reason_code='DEFAULT_OFF',"
            "updated_at=%s WHERE scope='testnet-global'",
            (now,),
        )
        connection.execute(
            "INSERT INTO testnet_activations(activation_id,account_binding_id,generation_id,"
            "gateway_instance_id,build_digest,configuration_digest,allowlist_digest,actor_id,"
            "session_digest,csrf_token_digest,origin_hash,status,reason,activated_at,expires_at,"
            "revoked_at,version,payload_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,'operator-local-1',"
            "%s,%s,%s,'ACTIVE','offline integration authority',%s,%s,NULL,1,%s)",
            (
                ACTIVATION_ID,
                ACCOUNT_ID,
                GENERATION_ID,
                "6" * 64,
                "7" * 64,
                CONFIGURATION_DIGEST,
                ALLOWLIST_DIGEST,
                "8" * 64,
                "9" * 64,
                "0" * 64,
                now,
                now + timedelta(minutes=30),
                "1" * 64,
            ),
        )
        connection.execute(
            "INSERT INTO testnet_reconciliation_checkpoints(checkpoint_id,generation_id,"
            "checkpoint_digest,observation_set_digest,ledger_snapshot_digest,status,"
            "mismatch_codes,operator_confirmation_digest,watermark_at,created_at,version) "
            "VALUES (%s,%s,%s,%s,%s,'HEALTHY',ARRAY[]::varchar[],NULL,%s,%s,1)",
            ("2" * 64, GENERATION_ID, "3" * 64, "4" * 64, "5" * 64, now, now),
        )
    connection.execute(
        "INSERT INTO testnet_gateway_commands(command_id,authorization_id,approval_id,"
        "generation_id,client_order_id,command_type,effect_class,capability_id,idempotency_key,"
        "request_digest,command,issued_at,expires_at) VALUES (%s,%s,%s,%s,%s,"
        "'SUBMIT_LIMIT_ORDER','CREATE_ORDER','SPOT_TESTNET_SUBMIT_LIMIT_GTC',%s,%s,%s,%s,%s)",
        (
            command_id,
            payload["original_authorization_id"],
            payload["original_approval_id"],
            GENERATION_ID,
            client_order_id,
            payload["idempotency_key"],
            request_digest,
            Jsonb(payload),
            now,
            now + timedelta(minutes=5),
        ),
    )
    connection.execute("SET session_replication_role='origin'")


def test_offline_preflight_reconciliation_materialization_and_same_id_unknown_resolution(
    postgres: None,
) -> None:
    del postgres
    now = datetime.now(UTC).replace(microsecond=0)
    session_digest = canonical_digest(["p8-offline-session"])
    activation_csrf = canonical_digest(["p8-offline-activation-csrf"])
    approval_csrf = canonical_digest(["p8-offline-approval-csrf"])
    origin_hash = canonical_digest(["p8-offline-origin"])
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO local_operators(actor_id,argon2id_phc,created_at) "
            "VALUES ('operator-local-1',%s,%s) ON CONFLICT (actor_id) DO NOTHING",
            ("$argon2id$" + "p" * 40, now - timedelta(minutes=2)),
        )
        connection.execute(
            "INSERT INTO operator_sessions(session_digest,actor_id,issued_at,last_seen_at,"
            "idle_expires_at,absolute_expires_at,revoked_at) VALUES (%s,'operator-local-1',"
            "%s,%s,%s,%s,NULL)",
            (
                session_digest,
                now - timedelta(minutes=2),
                now - timedelta(minutes=1),
                now + timedelta(minutes=20),
                now + timedelta(hours=1),
            ),
        )
        for csrf in (activation_csrf, approval_csrf):
            connection.execute(
                "INSERT INTO session_csrf_tokens(csrf_token_digest,session_digest,issued_at,"
                "expires_at,consumed_at) VALUES (%s,%s,%s,%s,%s)",
                (csrf, session_digest, now, now + timedelta(minutes=10), now),
            )
        connection.execute("SET session_replication_role='origin'")

    room = PostgresTestnetOperatorRoom(CONTROL_URL)
    initial = room.operator_state()
    assert initial["account_binding_id"] == LOCAL_ACCOUNT_ID
    assert initial["activate_action_allowed"] is True
    activation_intent = room.activate(
        int(initial["activation_version"]),
        str(initial["activation_view_digest"]),
        "오프라인 대조를 위한 명시적 Testnet 사전 활성화",
        idempotency_key="phase8-offline-preflight",
        session_binding_hash=session_digest,
        csrf_binding_hash=activation_csrf,
        origin_hash=origin_hash,
    )
    assert activation_intent.status_code == 201
    intent_worker = PostgresIntentWorker(
        EXECUTION_URL,
        ExecutionRuntimeBinding(
            gateway_instance_id="6" * 64,
            build_digest="7" * 64,
            configuration_digest=CONFIGURATION_DIGEST,
            allowlist_digest=ALLOWLIST_DIGEST,
        ),
    )
    activated = intent_worker.run_once(now=now + timedelta(seconds=1))
    assert activated is not None and activated["outcome"] == "ACTIVATION_PREFLIGHT_STARTED"
    preflight = room.operator_state()
    assert preflight["gateway_health"] == "ACTIVATING"
    assert preflight["testnet_barrier_status"] == "ACTIVE"

    writer = PostgresGatewayObservationWriter(GATEWAY_URL, runtime=_gateway_runtime())
    observations = [
        {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "SERVER_TIME",
            "capability_id": "SPOT_TESTNET_TIME",
            "authoritative": True,
            "server_time_ms": int(now.timestamp() * 1000),
        },
        {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "ACCOUNT_SNAPSHOT",
            "capability_id": "SPOT_TESTNET_ACCOUNT",
            "authoritative": True,
            "event_time_ms": int(now.timestamp() * 1000),
            "balances": [
                {"asset": "USDT", "free": "1000", "locked": "0"},
                {"asset": "BTC", "free": "1", "locked": "0"},
                {"asset": "ETH", "free": "10", "locked": "0"},
            ],
        },
        {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "OPEN_ORDERS_SNAPSHOT",
            "capability_id": "SPOT_TESTNET_OPEN_ORDERS_BY_SYMBOL",
            "authoritative": True,
            "requested_symbol": "BTCUSDT",
            "orders": [],
        },
        {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "OPEN_ORDERS_SNAPSHOT",
            "capability_id": "SPOT_TESTNET_OPEN_ORDERS_BY_SYMBOL",
            "authoritative": True,
            "requested_symbol": "ETHUSDT",
            "orders": [],
        },
    ]
    for index, observation in enumerate(observations, 2):
        writer.record(observation, source_channel="REST", now=now + timedelta(seconds=index))
    reconciliation = PostgresReconciliationWorker(EXECUTION_URL)
    server_time = reconciliation.run_once(now=now + timedelta(seconds=6))
    assert server_time is not None and server_time["outcome"] == "SERVER_TIME_OBSERVED"
    assert reconciliation.run_once(now=now + timedelta(seconds=6)) is not None
    healthy = reconciliation.run_once(now=now + timedelta(seconds=7))
    assert healthy is not None
    healthy = reconciliation.run_once(now=now + timedelta(seconds=8))
    assert healthy is not None and healthy["outcome"] == "HEALTHY"
    ready = room.operator_state()
    assert ready["new_commands_allowed"] is True

    proposal_id = canonical_digest(["p8-offline-proposal"])
    proposal_hash = canonical_digest(["p8-offline-proposal-hash"])
    source_decision_id = canonical_digest(["p8-offline-source-decision"])
    evidence_id = canonical_digest(["p8-offline-evidence"])
    evidence_hash = canonical_digest(["p8-offline-evidence-hash"])
    paper_preview: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.001",
        "limit_price": "60000",
    }
    paper_preview["paper_order_preview_hash"] = canonical_digest(paper_preview)
    source_input = {
        "proposal": {"proposal_hash": proposal_hash},
        "data": {
            "evidence_hash": evidence_hash,
            "as_of": now.isoformat().replace("+00:00", "Z"),
            "knowledge_cutoff": now.isoformat().replace("+00:00", "Z"),
            "freshness": "FRESH",
            "quality": "HEALTHY",
            "future_contamination": False,
            "watermark_complete": True,
        },
        "order_preview": paper_preview,
    }
    source_input_digest = canonical_digest(source_input)
    source_decision_hash = canonical_digest(
        {
            "decision_schema_version": "woozoo.risk-decision/v1",
            "risk_input_digest": source_input_digest,
            "verdict": "ALLOWED",
            "ordered_reason_codes": ["RISK_ALLOWED"],
        }
    )
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO trade_proposals(proposal_id,run_id,evidence_id,proposal_version,side,"
            "risk_eligible,proposal_hash,payload,created_at) VALUES (%s,%s,%s,'v1','BUY',true,"
            "%s,%s,%s)",
            (
                proposal_id,
                canonical_digest(["p8-offline-run"]),
                evidence_id,
                proposal_hash,
                Jsonb({}),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO risk_decisions(decision_id,risk_input_digest,risk_input,decision_hash,"
            "verdict,primary_reason,ordered_reason_codes,policy_version,proposal_hash,"
            "portfolio_snapshot_hash,data_state_hash,paper_order_preview_hash,"
            "reconciliation_checkpoint_hash,kill_switch_version,decision_as_of,recorded_at) "
            "VALUES (%s,%s,%s,%s,'ALLOWED','RISK_ALLOWED',%s,'woozoo.risk-policy/v1',%s,"
            "%s,%s,%s,%s,0,%s,%s)",
            (
                source_decision_id,
                source_input_digest,
                Jsonb(source_input),
                source_decision_hash,
                Jsonb(["RISK_ALLOWED"]),
                proposal_hash,
                canonical_digest(["p8-offline-portfolio"]),
                canonical_digest(["p8-offline-data"]),
                paper_preview["paper_order_preview_hash"],
                ready["reconciliation_checkpoint_digest"],
                now,
                now,
            ),
        )
        connection.execute("SET session_replication_role='origin'")
    materialized = PostgresTestnetRiskMaterializer(EXECUTION_URL).run_once(
        now=now + timedelta(seconds=8)
    )
    assert materialized is not None and materialized["outcome"] == "TESTNET_RISK_MATERIALIZED"
    approval_view = room.approval_view(proposal_id)
    approval_intent = room.decide_approval(
        proposal_id=proposal_id,
        decision="APPROVE",
        expected_version=int(approval_view["view_version"]),
        preview_digest=str(approval_view["testnet_order_preview_digest"]),
        approval_input_digest=str(approval_view["approval_input_digest"]),
        reason="오프라인 UNKNOWN 동일 식별자 대조 검증",
        idempotency_key="phase8-offline-approval",
        session_binding_hash=session_digest,
        csrf_binding_hash=approval_csrf,
        origin_hash=origin_hash,
    )
    assert approval_intent.status_code == 201
    issued = intent_worker.run_once(now=now + timedelta(seconds=9))
    assert issued is not None and issued["outcome"] == "AUTHORIZATION_ISSUED"
    execution_id = str(issued["execution_id"])

    sent_types: list[str] = []

    def unknown(payload: dict[str, object]) -> str:
        sent_types.append(str(payload["command_type"]))
        return "SUBMISSION_UNKNOWN"

    submitted = PostgresGatewayDispatcher(GATEWAY_URL, runtime=_gateway_runtime()).run_once(
        unknown, now=now + timedelta(seconds=10)
    )
    assert submitted is not None and submitted["status"] == "SUBMISSION_UNKNOWN"
    query_one = reconciliation.run_once(now=now + timedelta(seconds=11))
    assert query_one is not None and query_one["outcome"] == "UNKNOWN_QUERY_ISSUED"

    not_found_observation = {
        "schema_version": "woozoo.testnet-gateway-observation/v1",
        "kind": "ORDER_QUERY",
        "capability_id": "SPOT_TESTNET_QUERY_BY_CLIENT_ID",
        "authoritative": True,
        "found": False,
        "order": None,
    }

    def not_found(payload: dict[str, object]) -> SanitizedTransportResult:
        sent_types.append(str(payload["command_type"]))
        return SanitizedTransportResult(
            "EXCHANGE_ACKNOWLEDGED",
            400,
            -2013,
            canonical_digest(["not-found", payload["command_id"]]),
            not_found_observation,
        )

    first_query_receipt = PostgresGatewayDispatcher(
        GATEWAY_URL, runtime=_gateway_runtime()
    ).run_once(not_found, now=now + timedelta(seconds=12))
    assert first_query_receipt is not None and first_query_receipt.get("observation_id")
    first_absence = reconciliation.run_once(now=now + timedelta(seconds=13))
    assert first_absence is not None and first_absence["outcome"] == "SUBMISSION_UNKNOWN"
    query_two = reconciliation.run_once(now=now + timedelta(seconds=14))
    assert query_two is not None and query_two["outcome"] == "UNKNOWN_QUERY_ISSUED"
    second_query_receipt = PostgresGatewayDispatcher(
        GATEWAY_URL, runtime=_gateway_runtime()
    ).run_once(not_found, now=now + timedelta(seconds=15))
    assert second_query_receipt is not None and second_query_receipt.get("observation_id")
    resolved = reconciliation.run_once(now=now + timedelta(seconds=16))
    assert resolved is not None and resolved["outcome"] == "NOT_FOUND_CONFIRMED"

    with psycopg.connect(DATABASE_URL) as connection:
        commands = connection.execute(
            "SELECT command_type,client_order_id,command FROM testnet_gateway_commands "
            "WHERE command_id=%s OR command->>'correlation_id'=%s ORDER BY issued_at",
            (execution_id, execution_id),
        ).fetchall()
        assert [item[0] for item in commands] == [
            "SUBMIT_LIMIT_ORDER",
            "QUERY_EXISTING_ORDER",
            "QUERY_EXISTING_ORDER",
        ]
        assert len({item[1] for item in commands}) == 1
        schema = json.loads(
            (ROOT / "packages/contracts/spec/testnet-gateway-command.v1.json").read_text("utf-8")
        )
        for item in commands:
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(item[2])
        assert connection.execute(
            "SELECT external_outcome FROM testnet_orders WHERE command_id=%s", (execution_id,)
        ).fetchone() == ("NOT_FOUND_CONFIRMED",)
    assert sent_types == [
        "SUBMIT_LIMIT_ORDER",
        "QUERY_EXISTING_ORDER",
        "QUERY_EXISTING_ORDER",
    ]


def test_gateway_role_write_ahead_and_crash_recovery_never_resend(
    postgres: None,
) -> None:
    del postgres
    now = datetime.now(UTC)
    with psycopg.connect(DATABASE_URL) as connection:
        _seed_authority_and_command(
            connection,
            command_id="6" * 64,
            request_digest="7" * 64,
            client_order_id="wz8-" + "8" * 32,
            now=now,
        )

    sends: list[str] = []

    def acknowledged(payload: dict[str, object]) -> str:
        sends.append(str(payload["client_order_id"]))
        return "EXCHANGE_ACKNOWLEDGED"

    result = PostgresGatewayDispatcher(GATEWAY_URL, runtime=_gateway_runtime()).run_once(
        acknowledged, now=now + timedelta(seconds=1)
    )
    assert result is not None and result["status"] == "EXCHANGE_ACKNOWLEDGED"
    assert sends == ["wz8-" + "8" * 32]
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM testnet_gateway_dispatch_attempts WHERE command_id=%s",
            ("6" * 64,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT array_agg(status ORDER BY observed_at) FROM testnet_gateway_receipts "
            "WHERE command_id=%s",
            ("6" * 64,),
        ).fetchone() == (["DISPATCH_RECORDED", "EXCHANGE_ACKNOWLEDGED"],)

        _seed_authority_and_command(
            connection,
            command_id="9" * 64,
            request_digest="8" * 64,
            client_order_id="wz8-" + "7" * 32,
            now=now,
        )
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO testnet_gateway_inbox(command_id,request_digest,received_at) "
            "VALUES (%s,%s,%s)",
            ("9" * 64, "8" * 64, now),
        )
        connection.execute(
            "INSERT INTO testnet_gateway_dispatch_attempts(attempt_id,command_id,client_order_id,"
            "status,started_at) VALUES (%s,%s,%s,'DISPATCH_RECORDED',%s)",
            ("0" * 64, "9" * 64, "wz8-" + "7" * 32, now),
        )
        connection.execute(
            "INSERT INTO testnet_gateway_receipts(receipt_id,command_id,request_digest,status,"
            "external_effect_count,receipt,observed_at) VALUES (%s,%s,%s,'DISPATCH_RECORDED',0,"
            "%s,%s)",
            (
                HASH,
                "9" * 64,
                "8" * 64,
                Jsonb({"status": "DISPATCH_RECORDED"}),
                now,
            ),
        )
        connection.execute("SET session_replication_role='origin'")

    crash_calls = 0

    def must_not_send(_: dict[str, object]) -> str:
        nonlocal crash_calls
        crash_calls += 1
        return "EXCHANGE_ACKNOWLEDGED"

    recovered = PostgresGatewayDispatcher(GATEWAY_URL, runtime=_gateway_runtime()).run_once(
        must_not_send, now=now + timedelta(seconds=2)
    )
    assert recovered is not None and recovered["status"] == "SUBMISSION_UNKNOWN"
    assert recovered["client_order_id"] == "wz8-" + "7" * 32
    assert crash_calls == 0


def test_control_intent_to_execution_command_is_atomic_and_schema_valid(
    postgres: None,
) -> None:
    del postgres
    now = datetime.now(UTC)
    proposal_id = "a" * 64
    proposal_hash = "b" * 64
    decision_id = "d" * 64
    session_digest = "1" * 64
    csrf_digest = "2" * 64
    origin_hash = "3" * 64
    client_order_id = "wz8-" + "6" * 32
    preview = {
        "schema_version": "woozoo.testnet-order-preview/v1",
        "preview_policy_version": "woozoo.testnet-preview-policy/v1",
        "environment": "BINANCE_SPOT_TESTNET",
        "account_binding_id": ACCOUNT_ID,
        "account_generation": 1,
        "proposal_id": proposal_id,
        "proposal_hash": proposal_hash,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.001",
        "limit_price": "60000.00",
        "client_order_id": client_order_id,
    }
    preview_digest = canonical_digest(preview)
    preview["testnet_order_preview_digest"] = preview_digest
    source_risk_input: dict[str, object] = {}
    source_risk_input_digest = canonical_digest(source_risk_input)
    source_decision_id = "e" * 64
    source_decision_hash = canonical_digest(
        {
            "decision_schema_version": "woozoo.risk-decision/v1",
            "risk_input_digest": source_risk_input_digest,
            "verdict": "ALLOWED",
            "ordered_reason_codes": ["RISK_ALLOWED"],
        }
    )
    risk_input = {
        "proposal_hash": proposal_hash,
        "account_binding_id": ACCOUNT_ID,
        "account_generation": 1,
        "reconciliation_checkpoint_digest": "3" * 64,
        "testnet_barrier_version": 1,
        "paper_kill_version": 0,
        "source_risk_decision_id": source_decision_id,
        "source_risk_decision_hash": source_decision_hash,
    }
    risk_input_digest = canonical_digest(risk_input)
    decision_material = {
        "schema_version": "woozoo.testnet-risk-decision/v1",
        "decision_id": decision_id,
        "risk_input_digest": risk_input_digest,
        "proposal_id": proposal_id,
        "preview_digest": preview_digest,
        "generation_id": GENERATION_ID,
        "verdict": "ALLOWED",
        "policy_version": "woozoo.testnet-risk-policy/v1",
        "ordered_reason_codes": ["RISK_ALLOWED"],
        "decided_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    decision_hash = canonical_digest(decision_material)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("SET session_replication_role='replica'")
        connection.execute(
            "INSERT INTO local_operators(actor_id,argon2id_phc,created_at) "
            "VALUES ('operator-local-1',%s,%s) ON CONFLICT (actor_id) DO NOTHING",
            ("$argon2id$" + "x" * 40, now - timedelta(minutes=2)),
        )
        connection.execute(
            "INSERT INTO operator_sessions(session_digest,actor_id,issued_at,last_seen_at,"
            "idle_expires_at,absolute_expires_at,revoked_at) VALUES (%s,'operator-local-1',"
            "%s,%s,%s,%s,NULL)",
            (
                session_digest,
                now - timedelta(minutes=2),
                now - timedelta(minutes=1),
                now + timedelta(minutes=10),
                now + timedelta(hours=1),
            ),
        )
        connection.execute(
            "INSERT INTO session_csrf_tokens(csrf_token_digest,session_digest,issued_at,"
            "expires_at,consumed_at) VALUES (%s,%s,%s,%s,%s)",
            (csrf_digest, session_digest, now, now + timedelta(minutes=10), now),
        )
        connection.execute(
            "INSERT INTO trade_proposals(proposal_id,run_id,evidence_id,proposal_version,side,"
            "risk_eligible,proposal_hash,payload,created_at) VALUES (%s,%s,%s,'v1','BUY',true,"
            "%s,%s,%s)",
            (proposal_id, "4" * 64, "5" * 64, proposal_hash, Jsonb({}), now),
        )
        connection.execute(
            "INSERT INTO risk_decisions(decision_id,risk_input_digest,risk_input,decision_hash,"
            "verdict,primary_reason,ordered_reason_codes,policy_version,proposal_hash,"
            "portfolio_snapshot_hash,data_state_hash,paper_order_preview_hash,"
            "reconciliation_checkpoint_hash,kill_switch_version,decision_as_of,recorded_at) "
            "VALUES (%s,%s,%s,%s,'ALLOWED','RISK_ALLOWED',%s,'woozoo.risk-policy/v1',%s,"
            "%s,%s,%s,%s,0,%s,%s)",
            (
                source_decision_id,
                source_risk_input_digest,
                Jsonb(source_risk_input),
                source_decision_hash,
                Jsonb(["RISK_ALLOWED"]),
                proposal_hash,
                "6" * 64,
                "7" * 64,
                "8" * 64,
                "9" * 64,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO testnet_order_previews(preview_digest,proposal_id,proposal_hash,"
            "generation_id,symbol,side,quantity,limit_price,client_order_id,preview,created_at,"
            "expires_at) VALUES (%s,%s,%s,%s,'BTCUSDT','BUY',0.001,60000,%s,%s,%s,%s)",
            (
                preview_digest,
                proposal_id,
                proposal_hash,
                GENERATION_ID,
                client_order_id,
                Jsonb(preview),
                now,
                now + timedelta(minutes=5),
            ),
        )
        connection.execute(
            "INSERT INTO testnet_risk_decisions(decision_id,decision_hash,risk_input_digest,"
            "risk_input,proposal_id,preview_digest,generation_id,verdict,policy_version,"
            "ordered_reason_codes,decided_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'ALLOWED',"
            "'woozoo.testnet-risk-policy/v1',ARRAY['RISK_ALLOWED']::varchar[],%s,%s)",
            (
                decision_id,
                decision_hash,
                risk_input_digest,
                Jsonb(risk_input),
                proposal_id,
                preview_digest,
                GENERATION_ID,
                now,
                now + timedelta(minutes=5),
            ),
        )
        connection.execute("SET session_replication_role='origin'")

    room = PostgresTestnetOperatorRoom(CONTROL_URL)
    view = room.approval_view(proposal_id)
    intent = room.decide_approval(
        proposal_id=proposal_id,
        decision="APPROVE",
        expected_version=int(view["view_version"]),
        preview_digest=str(view["testnet_order_preview_digest"]),
        approval_input_digest=str(view["approval_input_digest"]),
        reason="오프라인 통합 테스트에서 별도 Testnet 승인을 확인",
        idempotency_key="phase8-real-intent-chain",
        session_binding_hash=session_digest,
        csrf_binding_hash=csrf_digest,
        origin_hash=origin_hash,
    )
    assert intent.status_code == 201
    result = PostgresIntentWorker(
        EXECUTION_URL,
        ExecutionRuntimeBinding(
            gateway_instance_id="6" * 64,
            build_digest="7" * 64,
            configuration_digest=CONFIGURATION_DIGEST,
            allowlist_digest=ALLOWLIST_DIGEST,
        ),
    ).run_once(now=now + timedelta(seconds=1))
    assert result is not None and result["outcome"] == "AUTHORIZATION_ISSUED"

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT command,request_digest FROM testnet_gateway_commands WHERE client_order_id=%s",
            (client_order_id,),
        ).fetchone()
        assert row is not None
        command = row[0]
        assert command["request_digest"] == row[1]
        schema = json.loads(
            (ROOT / "packages/contracts/spec/testnet-gateway-command.v1.json").read_text("utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(command)
        intent_id = intent.body["intent_id"]
        assert connection.execute(
            "SELECT count(*) FROM testnet_operator_intent_results WHERE intent_id=%s",
            (intent_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM testnet_domain_events WHERE payload->>'intent_id'=%s",
            (intent_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM testnet_outbox WHERE payload->>'intent_id'=%s",
            (intent_id,),
        ).fetchone() == (1,)


def test_user_data_fill_is_idempotent_and_posts_db_enforced_balanced_ledger(
    postgres: None,
) -> None:
    del postgres
    with psycopg.connect(DATABASE_URL) as connection:
        pending_issued_at = connection.execute(
            "SELECT issued_at FROM testnet_pending_gateway_commands_v1 ORDER BY issued_at LIMIT 1"
        ).fetchone()
    assert pending_issued_at is not None
    now = pending_issued_at[0] + timedelta(seconds=1)
    sent_client_order_id = ""

    def acknowledged(payload: dict[str, object]) -> SanitizedTransportResult:
        nonlocal sent_client_order_id
        sent_client_order_id = str(payload["client_order_id"])
        return SanitizedTransportResult(
            "EXCHANGE_ACKNOWLEDGED",
            200,
            None,
            canonical_digest(["submit-ack", payload["command_id"]]),
            {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "ORDER",
                "capability_id": "SPOT_TESTNET_SUBMIT_LIMIT_GTC",
                "authoritative": True,
                "found": True,
                "order": {
                    "client_order_id": payload["client_order_id"],
                    "exchange_order_id": "12345",
                    "symbol": payload["symbol"],
                    "side": payload["side"],
                    "status": "NEW",
                    "quantity": payload["quantity"],
                    "limit_price": payload["limit_price"],
                    "cumulative_filled_quantity": "0",
                    "event_time_ms": int(now.timestamp() * 1000),
                },
            },
        )

    dispatched = PostgresGatewayDispatcher(GATEWAY_URL, runtime=_gateway_runtime()).run_once(
        acknowledged, now=now
    )
    assert dispatched is not None and dispatched["status"] == "EXCHANGE_ACKNOWLEDGED"
    reconciliation = PostgresReconciliationWorker(EXECUTION_URL)
    observed = reconciliation.run_once(now=now + timedelta(seconds=1))
    assert observed is not None and observed["outcome"] == "ORDER_RECONCILED"

    user_data = {
        "schema_version": "woozoo.testnet-gateway-observation/v1",
        "kind": "USER_DATA_EXECUTION",
        "capability_id": "SPOT_TESTNET_USER_DATA",
        "authoritative": True,
        "subscription_id": 1,
        "order": {
            "client_order_id": sent_client_order_id,
            "exchange_order_id": "12345",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "status": "FILLED",
            "quantity": "0.001",
            "limit_price": "60000",
            "cumulative_filled_quantity": "0.001",
            "event_time_ms": int((now + timedelta(seconds=2)).timestamp() * 1000),
        },
        "fill": {
            "external_trade_id": "67890",
            "exchange_order_id": "12345",
            "symbol": "BTCUSDT",
            "quantity": "0.001",
            "price": "60000",
            "fee_amount": "0.06",
            "fee_asset": "USDT",
            "event_time_ms": int((now + timedelta(seconds=2)).timestamp() * 1000),
        },
    }
    writer = PostgresGatewayObservationWriter(GATEWAY_URL, runtime=_gateway_runtime())
    first_observation_id = writer.record(
        user_data, source_channel="USER_DATA", now=now + timedelta(seconds=2)
    )
    filled = reconciliation.run_once(now=now + timedelta(seconds=3))
    assert filled is not None and filled["outcome"] == "ORDER_RECONCILED"
    assert filled["new_fill_count"] == 1
    assert (
        writer.record(user_data, source_channel="USER_DATA", now=now + timedelta(seconds=2))
        == first_observation_id
    )
    assert reconciliation.run_once(now=now + timedelta(seconds=4)) is None

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT status,filled_quantity,external_outcome FROM testnet_orders "
            "WHERE client_order_id=%s",
            (sent_client_order_id,),
        ).fetchone() == ("FILLED", Decimal("0.001"), "FOUND")
        assert connection.execute(
            "SELECT count(*) FROM testnet_fills WHERE external_trade_id='67890'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT commodity,sum(debit)-sum(credit) FROM testnet_ledger_entries "
            "GROUP BY transaction_id,commodity ORDER BY commodity"
        ).fetchall() == [("BTC", Decimal(0)), ("USDT", Decimal(0))]

    unbalanced_transaction = canonical_digest(["p8-unbalanced-transaction"])
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(EXECUTION_URL) as connection:
            connection.execute(
                "INSERT INTO testnet_ledger_transactions(transaction_id,generation_id,"
                "business_event_id,journal_kind,posted_at) VALUES (%s,%s,%s,'FILL',%s)",
                (
                    unbalanced_transaction,
                    GENERATION_ID,
                    canonical_digest(["p8-unbalanced-event"]),
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO testnet_ledger_entries(transaction_id,line_no,ledger_account,"
                "commodity,debit,credit) VALUES (%s,1,'asset:BTC:available','BTC',1,0)",
                (unbalanced_transaction,),
            )


@pytest.mark.parametrize(
    "table", ["local_operators", "testnet_risk_decisions", "testnet_ledger_entries"]
)
def test_gateway_role_cannot_read_actor_risk_or_ledger(postgres: None, table: str) -> None:
    del postgres
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(GATEWAY_URL) as connection:
            connection.execute(f"SELECT * FROM {table} LIMIT 1")
