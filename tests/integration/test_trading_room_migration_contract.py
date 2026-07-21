from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import subprocess
import sys
from threading import Event

import psycopg
from psycopg.types.json import Jsonb
import pytest

from docker_infrastructure_lock import docker_infrastructure_lock
from paper_engine.persistence import PostgresPaperStore
from risk_engine import KillActivation, PostgresKillSwitch


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260720_0007_trading_room.py"
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
PAPER_WRITER_URL = "postgresql://woozoo_paper_engine@127.0.0.1:5433/woozoo"
PAPER_ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"
ENVIRONMENT = {"DATABASE_URL": DATABASE_URL, "TRADING_MODE": "paper"}


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def test_phase7_migration_closes_auth_approval_authorization_and_recovery_boundaries() -> None:
    source = MIGRATION.read_text("utf-8")

    for table in (
        "local_operators",
        "operator_sessions",
        "session_csrf_tokens",
        "paper_approvals",
        "paper_approval_revocations",
        "paper_execution_authorizations",
        "kill_recovery_events",
        "risk_kill_recovery_command_receipts",
    ):
        assert f'"{table}"' in source
    assert 'sa.Column("session_digest"' in source
    assert 'sa.Column("csrf_token_digest"' in source
    assert 'sa.Column("session_token"' not in source
    assert 'sa.Column("csrf_token"' not in source
    assert "argon2id" in source
    assert "operator-local-1" in source
    assert "paper_execution_authorization_id" in source
    assert "fk_paper_attempt_phase7_authorization" in source
    assert "namespace IN ('test','paper')" in source
    assert "expires_at<=decided_at + interval '5 minutes'" in source
    assert "expires_at<=issued_at + interval '5 minutes'" in source
    assert "reject_risk_history_mutation" in source
    assert "paper_approval_view_v1" in source
    assert "paper_authorization_view_v1" in source
    assert "paper_order_reader_v1" in source
    assert "kill_switch_reader_v1" in source
    assert "paper_authorization_worker_reader_v1" in source
    assert "paper_pending_kill_activations_v1" in source
    assert "paper_recorded_book_market_is_current_v1" in source
    assert (
        "REVOKE ALL ON FUNCTION paper_recorded_book_market_is_current_v1(varchar) FROM PUBLIC"
        in source
    )
    assert "GRANT EXECUTE ON FUNCTION paper_recorded_book_market_is_current_v1(varchar) " in source
    recorded_book_guard = source.split(
        "CREATE FUNCTION paper_recorded_book_market_is_current_v1", 1
    )[1].split("CREATE FUNCTION enforce_phase7_attempt_binding", 1)[0]
    assert recorded_book_guard.index(
        "PERFORM 1 FROM stream_watermark_projections watermark"
    ) < recorded_book_guard.index("PERFORM 1 FROM market_status_projections market")
    assert recorded_book_guard.index(
        "PERFORM 1 FROM market_status_projections market"
    ) < recorded_book_guard.index("PERFORM 1 FROM collector_sessions collector")
    assert recorded_book_guard.index("PERFORM 1 FROM collector_sessions collector") < (
        recorded_book_guard.index("LOCK TABLE collector_sessions IN SHARE MODE")
    )
    assert "LOCK TABLE collector_sessions IN SHARE MODE NOWAIT" in recorded_book_guard
    assert recorded_book_guard.index("LOCK TABLE collector_sessions IN SHARE MODE NOWAIT") < (
        recorded_book_guard.index("observed_now := clock_timestamp()")
    )
    assert recorded_book_guard.index("LOCK TABLE collector_sessions IN SHARE MODE") < (
        recorded_book_guard.index("SELECT latest.id FROM collector_sessions latest")
    )
    assert "digest(source_identity.payload_bytes,'sha256')" in recorded_book_guard
    assert "source_identity.payload_hash<>source_identity.raw_payload_hash" in recorded_book_guard
    assert "book_payload->>'s'<>source_identity.symbol" in recorded_book_guard
    assert "(book_payload->>'u')::bigint<>source_identity.sequence" in recorded_book_guard
    assert "source_identity.normalized_event_id<>encode(digest" in recorded_book_guard
    for normalized_field, raw_field in (
        ("bid_price", "b"),
        ("bid_quantity", "B"),
        ("ask_price", "a"),
        ("ask_quantity", "A"),
    ):
        assert f"(source_identity.payload->>'{normalized_field}')::numeric<>" in recorded_book_guard
        assert f"(book_payload->>'{raw_field}')::numeric" in recorded_book_guard
    assert "WHEN OTHERS" not in recorded_book_guard
    assert "OR lock_not_available THEN" in recorded_book_guard
    assert 'sa.Column("phase7_response", postgresql.JSONB(), nullable=True)' in source
    assert "ck_phase7_observation_response" in source
    assert "enforce_phase7_observation_response_v1" in source
    assert "REVOKE ALL ON FUNCTION enforce_phase7_observation_response_v1() FROM PUBLIC" in source
    assert "NEW.phase7_response->>'status'<>current_order_status" in source
    assert "trading_room_market_reader_v1" in source
    assert "SELECT projection.sequence" in source
    assert "trading_room_audit_projection" in source
    assert "pg_advisory_xact_lock" in source
    assert "row_number() OVER" not in source
    assert "risk_market_books jsonb" in source
    assert "decision.risk_input->'market_books'" in source
    assert "KILL_RECOVERY_WORKER_NOT_READY" in source
    assert "CURRENT_TIMESTAMP-interval '5 seconds'" in source
    completion_guard = source.split("CREATE FUNCTION enforce_paper_kill_cancel_completion_v1", 1)[
        1
    ].split("CREATE FUNCTION enforce_risk_approval_receipt", 1)[0]
    approval_guard = source.split("CREATE FUNCTION issue_paper_approval_v1", 1)[1].split(
        "CREATE FUNCTION revoke_paper_approval_v1", 1
    )[0]
    recovery_guard = source.split("CREATE FUNCTION recover_kill_switch_v1", 1)[1].split(
        "CREATE FUNCTION assert_phase7_risk_outbox_consistency", 1
    )[0]
    recovery_reader = source.split("CREATE VIEW kill_switch_recovery_reader_v1", 1)[1].split(
        "CREATE FUNCTION trading_room_public_audit_data_v1", 1
    )[0]
    assert "FROM paper_execution_authorizations authz" in completion_guard
    assert "authz.paper_account_id=NEW.paper_account_id" in completion_guard
    assert "FROM paper_authorization_attempts attempt" in completion_guard
    assert "attempt.namespace='paper'" in completion_guard
    assert "attempt.paper_execution_authorization_id=authz.authorization_id" in completion_guard
    assert "paper_recorded_book_market_is_current_v1(latest.id)" in recovery_guard
    assert "paper_recorded_book_market_is_current_v1(latest.id)" in recovery_reader
    for writer_guard, authority_comparison in (
        (approval_guard, "checkpoint_row.authority_sequence<>COALESCE(("),
        (recovery_guard, "checkpoint_row.authority_sequence=COALESCE(("),
    ):
        assert writer_guard.index(
            "'paper-account:{PAPER_DEFAULT_ACCOUNT_ID}',0"
        ) < writer_guard.index(authority_comparison)
        assert "FROM paper_outbox_links link" in writer_guard
        assert "JOIN outbox_events event USING(event_id)" in writer_guard
        assert "WHERE link.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'),0)" in writer_guard
    assert "AND health.reconciliation_status='HEALTHY'" in recovery_reader
    assert "checkpoint_row.input_digest=completion_row.state_digest" in recovery_guard
    assert "checkpoint.input_digest=completion.state_digest" in recovery_reader
    assert "checkpoint.authority_sequence<>authority.current_sequence" in source
    assert "SECURITY DEFINER SET search_path = pg_catalog, public" in source
    assert "kill_switch_state_phase7_transition" in source
    assert "automatic Kill recovery is forbidden" in source
    assert "GRANT INSERT ON paper_approvals, paper_approval_revocations" not in source
    assert "GRANT INSERT ON paper_execution_authorizations" not in source
    assert "GRANT EXECUTE ON FUNCTION issue_paper_approval_v1" in source
    assert "OR p_approval_nonce=p_authorization_nonce" in source
    assert "AND approval.approval_nonce<>NEW.authorization_nonce" in source
    assert "TO woozoo_risk_engine" in source
    assert "TO woozoo_control_api" in source
    assert "GRANT INSERT ON paper_execution_authorizations TO woozoo_paper_engine" not in source
    assert "candidate.symbol=paper_order.symbol AND candidate.side=paper_order.side" in source
    assert 'op.execute(f"REVOKE ALL ON {view} FROM PUBLIC")' in source


def test_approval_sql_authority_requires_allowed_risk_and_rechecks_worker_atomically() -> None:
    source = MIGRATION.read_text("utf-8")
    approval_binding = source.split("CREATE FUNCTION enforce_phase7_approval_binding", 1)[1].split(
        "CREATE FUNCTION enforce_phase7_revocation_binding", 1
    )[0]
    approval_command = source.split("CREATE FUNCTION issue_paper_approval_v1", 1)[1].split(
        "CREATE FUNCTION revoke_paper_approval_v1", 1
    )[0]
    risk_persistence = source.split("CREATE FUNCTION persist_risk_decision_v1", 1)[1].split(
        "CREATE FUNCTION load_authoritative_risk_context_v1", 1
    )[0]
    risk_evaluation = source.split("CREATE FUNCTION load_authoritative_risk_context_v1", 1)[
        1
    ].split("CREATE FUNCTION recover_kill_switch_v1", 1)[0]
    recovery_command = source.split("CREATE FUNCTION recover_kill_switch_v1", 1)[1].split(
        "CREATE FUNCTION assert_phase7_risk_outbox_consistency", 1
    )[0]
    first_attempt = source.split("CREATE FUNCTION paper_lock_execution_authorization_v1", 1)[
        1
    ].split("CREATE FUNCTION paper_validate_kill_activation_v1", 1)[0]
    attempt_binding = source.split("CREATE FUNCTION enforce_phase7_attempt_binding", 1)[1].split(
        "CREATE FUNCTION enforce_phase7_broker_input_binding", 1
    )[0]

    assert "AND decision.verdict='ALLOWED'" in approval_binding
    assert "risk-proposal:" in risk_persistence
    assert "risk-proposal:" in risk_evaluation
    assert "risk-proposal:" in approval_command
    assert "risk-proposal:" in first_attempt
    risk_books = risk_evaluation.split("'books'", 1)[1].split("'open_orders'", 1)[0]
    assert "SELECT DISTINCT ON (event.symbol) event.id,event.symbol,event.payload" in risk_books
    assert risk_books.index(") latest") < risk_books.index(
        "WHERE paper_recorded_book_market_is_current_v1(latest.id)"
    )
    assert risk_books.count("paper_recorded_book_market_is_current_v1(latest.id)") == 1
    assert approval_command.index(
        "'paper-account:{PAPER_DEFAULT_ACCOUNT_ID}',0"
    ) < approval_command.index("'risk-proposal:'||p_proposal_id")
    assert approval_command.index("'risk-proposal:'||p_proposal_id") < approval_command.index(
        "SELECT * INTO proposal_row FROM trade_proposals"
    )
    assert approval_command.index(
        "SELECT * INTO proposal_row FROM trade_proposals"
    ) < approval_command.index("SELECT * INTO kill_row FROM kill_switch_state")
    assert approval_command.index(
        "SELECT * INTO kill_row FROM kill_switch_state"
    ) < approval_command.index("FROM paper_authorization_worker_state")
    for latest_guard in (approval_binding, first_attempt, attempt_binding):
        assert "ORDER BY latest.recorded_at DESC,latest.decision_id DESC LIMIT 1" in latest_guard
    for blocker in (
        "expected_block_reason:='HASH_MISMATCH'",
        "expected_block_reason:='KILL_SWITCH_ACTIVE'",
        "expected_block_reason:='KILL_VERSION_MISMATCH'",
        "expected_block_reason:='AUTHORIZATION_EXPIRED'",
        "expected_block_reason:='AUTHORIZATION_REVOKED'",
    ):
        assert blocker in attempt_binding
    assert attempt_binding.index("expected_block_reason:='HASH_MISMATCH'") < attempt_binding.index(
        "expected_block_reason:='KILL_SWITCH_ACTIVE'"
    )
    assert attempt_binding.index(
        "expected_block_reason:='KILL_SWITCH_ACTIVE'"
    ) < attempt_binding.index("expected_block_reason:='KILL_VERSION_MISMATCH'")
    assert attempt_binding.index(
        "expected_block_reason:='KILL_VERSION_MISMATCH'"
    ) < attempt_binding.index("expected_block_reason:='AUTHORIZATION_EXPIRED'")
    assert attempt_binding.index(
        "expected_block_reason:='AUTHORIZATION_EXPIRED'"
    ) < attempt_binding.index("expected_block_reason:='AUTHORIZATION_REVOKED'")
    assert "NEW.created_at>=authorized_row.expires_at" in attempt_binding
    assert "NEW.reason_code<>expected_block_reason" in attempt_binding
    assert "NEW.decision='REJECTED' OR decision.verdict='ALLOWED'" not in approval_binding
    assert "OR decision_row.verdict<>'ALLOWED'" in approval_command
    assert "p_decision='APPROVED' AND decision_row.verdict<>'ALLOWED'" not in approval_command

    worker_guard = approval_command
    assert "FROM paper_authorization_worker_state" in worker_guard
    assert "worker_name='phase7-paper-authorization' FOR SHARE" in worker_guard
    assert "worker_row.status<>'RUNNING'" in worker_guard
    assert "worker_row.heartbeat_at>authority_at" in worker_guard
    assert "worker_row.heartbeat_at<authority_at-interval '2 minutes'" in worker_guard
    assert "RAISE EXCEPTION 'PAPER_WORKER_NOT_READY'" in worker_guard
    assert approval_command.index("FROM paper_authorization_worker_state") < approval_command.index(
        "INSERT INTO paper_approvals"
    )
    assert approval_command.index(
        "worker_name='phase7-paper-authorization' FOR SHARE"
    ) < approval_command.index("authority_at:=clock_timestamp()")
    assert approval_command.index("authority_at:=clock_timestamp()") < approval_command.index(
        "authority_at>=decision_row.decision_as_of+interval '5 minutes'"
    )
    assert approval_command.index(
        "authority_at>=decision_row.decision_as_of+interval '5 minutes'"
    ) < approval_command.index("KILL_SWITCH_DRIFT")
    assert approval_command.index("KILL_SWITCH_DRIFT") < approval_command.index(
        "RECONCILIATION_DRIFT"
    )
    assert approval_command.index("RECONCILIATION_DRIFT") < approval_command.index(
        "PAPER_WORKER_NOT_READY"
    )
    assert "authority_at>=decision_row.decision_as_of+interval '5 minutes'" in approval_command
    assert "expires_at:=authority_at+interval '5 minutes'" in approval_command
    assert (
        "VALUES (p_idempotency_key,p_request_hash,approval_id,response,authority_at)"
        in approval_command
    )

    assert "CURRENT_TIMESTAMP" not in recovery_command
    assert "worker_name='phase7-paper-authorization' FOR SHARE" in recovery_command
    assert recovery_command.index(
        "paper_recorded_book_market_is_current_v1(latest.id)"
    ) < recovery_command.index("worker_name='phase7-paper-authorization' FOR SHARE")
    assert recovery_command.index(
        "worker_name='phase7-paper-authorization' FOR SHARE"
    ) < recovery_command.index("authority_at:=clock_timestamp()")
    assert "worker_row.heartbeat_at>authority_at" in recovery_command
    assert "worker_row.heartbeat_at<authority_at-interval '2 minutes'" in recovery_command
    assert (
        "(book->>'received_at')::timestamptz<authority_at-interval '5 seconds'" in recovery_command
    )
    assert "response,authority_at" in recovery_command
    assert "to_jsonb(authority_at)" in recovery_command


def test_phase7_migration_preserves_frozen_namespaces_as_legacy_and_adds_paper() -> None:
    source = MIGRATION.read_text("utf-8")

    assert "DROP CONSTRAINT ck_p4_paper_account_test_only" in source
    assert "DROP CONSTRAINT ck_p4_authorization_test_only" in source
    assert "DROP CONSTRAINT ck_analysis_run_namespace" in source
    assert "ck_phase7_paper_account_namespace" in source
    assert "ck_phase7_authorization_namespace" in source
    assert "ck_phase7_analysis_run_namespace" in source
    assert "PAPER_AUTHORIZATION" in source
    assert "fk_paper_input_phase7_authorization" in source
    assert "Production Paper broker input is not hash-bound" in source
    assert "attempt.paper_execution_authorization_id=authz.authorization_id" in source
    assert "receipt.broker_seq=NEW.broker_seq" in source
    assert "paper.opening-equity" in source
    assert "10000" in source
    assert "'BTC',jsonb_build_object('available','0','held','0','version',0)" in source
    assert "'{PAPER_DEFAULT_ACCOUNT_ID}','BTC',0,0,0" not in source
    assert "risk-input/v3" in source
    assert "namespace='paper'" in source
    assert "namespace='test'" in source


def test_phase7_migration_upgrades_with_digest_only_storage_and_least_privilege() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "20260720_0007")
            with psycopg.connect(DATABASE_URL) as invalid_connection:
                with pytest.raises(psycopg.errors.RaiseException, match="APPROVAL_COMMAND_INVALID"):
                    invalid_connection.execute(
                        "SELECT * FROM issue_paper_approval_v1("
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            "phase7-equal-nonce",
                            "1" * 64,
                            "2" * 64,
                            "APPROVED",
                            1,
                            "3" * 64,
                            "operator-local-1",
                            "4" * 64,
                            "5" * 64,
                            "6" * 64,
                            "n" * 32,
                            "n" * 32,
                            datetime.fromisoformat("2026-07-20T00:00:00+00:00"),
                        ),
                    )
                invalid_connection.rollback()
            with psycopg.connect(DATABASE_URL) as connection:
                session_columns = {
                    row[0]
                    for row in connection.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='operator_sessions'"
                    )
                }
                csrf_columns = {
                    row[0]
                    for row in connection.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='session_csrf_tokens'"
                    )
                }
                assert "session_digest" in session_columns
                assert not {"session_token", "cookie_value", "raw_session"} & session_columns
                assert "csrf_token_digest" in csrf_columns
                assert not {"csrf_token", "raw_token"} & csrf_columns
                constraints = "\n".join(
                    row[0]
                    for row in connection.execute(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname IN ('ck_phase7_paper_account_namespace',"
                        "'ck_phase7_authorization_namespace',"
                        "'ck_phase7_attempt_authorization_link',"
                        "'ck_phase7_analysis_run_namespace') ORDER BY conname"
                    )
                )
                assert "'paper'" in constraints and "'test'" in constraints
                assert connection.execute(
                    "SELECT account_id,namespace FROM paper_accounts WHERE namespace='paper'"
                ).fetchone() == (
                    "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e",
                    "paper",
                )
                assert connection.execute(
                    "SELECT asset,available,held,version FROM paper_asset_balances "
                    "WHERE account_id=%s ORDER BY asset",
                    (PAPER_ACCOUNT_ID,),
                ).fetchall() == [
                    ("USDT", 10000, 0, 0),
                ]
                assert connection.execute(
                    "SELECT commodity,sum(debit),sum(credit) FROM paper_ledger_entries "
                    "WHERE transaction_id=%s GROUP BY commodity",
                    ("1b0ed16915be7b75e206c111184b2c450eb3fe498ff46b95130c5cf1f5baaf4c",),
                ).fetchone() == ("USDT", 10000, 10000)
                assert connection.execute(
                    "SELECT has_table_privilege('woozoo_control_api',"
                    "'paper_approval_view_v1','SELECT'),"
                    "has_table_privilege('woozoo_control_api',"
                    "'paper_execution_authorizations','INSERT'),"
                    "has_table_privilege('woozoo_risk_engine',"
                    "'paper_execution_authorizations','INSERT'),"
                    "has_table_privilege('woozoo_paper_engine',"
                    "'paper_execution_authorizations','INSERT')"
                ).fetchone() == (True, False, False, False)
                assert connection.execute(
                    "SELECT has_function_privilege('woozoo_paper_engine',"
                    "'paper_recorded_book_market_is_current_v1(varchar)','EXECUTE'),"
                    "has_function_privilege('woozoo_control_api',"
                    "'paper_recorded_book_market_is_current_v1(varchar)','EXECUTE'),"
                    "has_function_privilege('woozoo_control_reader',"
                    "'paper_recorded_book_market_is_current_v1(varchar)','EXECUTE'),"
                    "has_table_privilege('woozoo_paper_engine','raw_market_events','SELECT'),"
                    "has_table_privilege('woozoo_paper_engine','collector_sessions','SELECT'),"
                    "has_table_privilege('woozoo_paper_engine',"
                    "'stream_watermark_projections','SELECT'),"
                    "has_table_privilege('woozoo_paper_engine',"
                    "'market_status_projections','SELECT')"
                ).fetchone() == (True, True, True, False, False, False, False)
                assert connection.execute(
                    "SELECT has_table_privilege('woozoo_evidence_writer',"
                    "'raw_market_events','SELECT'),"
                    "has_column_privilege('woozoo_evidence_writer',"
                    "'raw_market_events','stream','SELECT'),"
                    "has_column_privilege('woozoo_evidence_writer',"
                    "'raw_market_events','payload_bytes','SELECT')"
                ).fetchone() == (False, True, False)
                relational_definition = connection.execute(
                    "SELECT pg_get_functiondef("
                    "'assert_paper_relational_consistency()'::regprocedure)"
                ).fetchone()[0]
                assert (
                    "candidate.symbol=paper_order.symbolANDcandidate.side=paper_order.side"
                    in "".join(relational_definition.split())
                )
                assert connection.execute(
                    "SELECT count(*) FROM information_schema.views WHERE table_schema='public' "
                    "AND table_name IN ('paper_approval_view_v1',"
                    "'paper_authorization_view_v1','paper_order_reader_v1',"
                    "'kill_switch_reader_v1','paper_authorization_worker_reader_v1',"
                    "'paper_pending_kill_activations_v1','trading_room_market_reader_v1')"
                ).fetchone() == (7,)
                assert connection.execute(
                    "SELECT count(*),min(account_id) FROM paper_accounts WHERE namespace='paper'"
                ).fetchone() == (1, PAPER_ACCOUNT_ID)
                first_event_id = "e" * 64
                connection.execute(
                    "INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,"
                    "occurred_at,aggregate_type,aggregate_id,aggregate_version) VALUES "
                    "(%s,'analysis.cursor.test',%s,%s,'2026-07-20T00:01:00Z',"
                    "'analysis',%s,1)",
                    (
                        first_event_id,
                        Jsonb({"producer": "agent-orchestrator", "data": {"ordinal": 1}}),
                        "1" * 64,
                        first_event_id,
                    ),
                )
                first_cursor = connection.execute(
                    "SELECT sequence FROM trading_room_audit_reader_v1 WHERE event_id=%s",
                    (first_event_id,),
                ).fetchone()[0]
                later_ids = ("f" * 64, "a" * 63 + "b")
                for event_id, occurred_at, ordinal in (
                    (later_ids[0], "2026-07-20T00:02:00Z", 2),
                    (later_ids[1], "2026-07-19T23:59:00Z", 3),
                ):
                    connection.execute(
                        "INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,"
                        "occurred_at,aggregate_type,aggregate_id,aggregate_version) VALUES "
                        "(%s,'analysis.cursor.test',%s,%s,%s,'analysis',%s,1)",
                        (
                            event_id,
                            Jsonb(
                                {
                                    "producer": "agent-orchestrator",
                                    "data": {"ordinal": ordinal},
                                }
                            ),
                            str(ordinal) * 64,
                            occurred_at,
                            event_id,
                        ),
                    )
                page = connection.execute(
                    "SELECT event_id FROM trading_room_audit_reader_v1 "
                    "WHERE sequence>%s AND event_id=ANY(%s) ORDER BY sequence",
                    (first_cursor, list(later_ids)),
                ).fetchall()
                assert page == [(later_ids[0],), (later_ids[1],)]
                assert len(set(page)) == 2
                connection.rollback()
            with psycopg.connect(PAPER_WRITER_URL) as paper_connection:
                assert paper_connection.execute(
                    "SELECT paper_recorded_book_market_is_current_v1(%s)", ("f" * 64,)
                ).fetchone() == (False,)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    paper_connection.execute("SELECT count(*) FROM market_status_projections")
                paper_connection.rollback()
            lower_event_id = "9" * 64
            higher_event_id = "8" * 64
            lower = psycopg.connect(DATABASE_URL)
            lower.execute(
                "INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,"
                "occurred_at,aggregate_type,aggregate_id,aggregate_version) VALUES "
                "(%s,'analysis.cursor.commit-safe',%s,%s,CURRENT_TIMESTAMP,"
                "'analysis',%s,1)",
                (
                    lower_event_id,
                    Jsonb({"producer": "agent-orchestrator", "data": {"ordinal": 1}}),
                    "9" * 64,
                    lower_event_id,
                ),
            )
            higher_started = Event()

            def commit_higher() -> None:
                with psycopg.connect(DATABASE_URL) as higher:
                    higher_started.set()
                    higher.execute(
                        "INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,"
                        "occurred_at,aggregate_type,aggregate_id,aggregate_version) VALUES "
                        "(%s,'analysis.cursor.commit-safe',%s,%s,CURRENT_TIMESTAMP,"
                        "'analysis',%s,1)",
                        (
                            higher_event_id,
                            Jsonb({"producer": "agent-orchestrator", "data": {"ordinal": 2}}),
                            "8" * 64,
                            higher_event_id,
                        ),
                    )

            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    higher_future = executor.submit(commit_higher)
                    assert higher_started.wait(timeout=5)
                    assert not higher_future.done()
                    lower.commit()
                    higher_future.result(timeout=5)
            finally:
                lower.close()
            with psycopg.connect(DATABASE_URL) as connection:
                committed_page = connection.execute(
                    "SELECT sequence,event_id FROM trading_room_audit_reader_v1 "
                    "WHERE event_id=ANY(%s) ORDER BY sequence",
                    ([lower_event_id, higher_event_id],),
                ).fetchall()
                assert [row[1] for row in committed_page] == [lower_event_id, higher_event_id]
                after_lower = connection.execute(
                    "SELECT event_id FROM trading_room_audit_reader_v1 "
                    "WHERE sequence>%s AND event_id=ANY(%s) ORDER BY sequence",
                    (committed_page[0][0], [lower_event_id, higher_event_id]),
                ).fetchall()
                assert after_lower == [(higher_event_id,)]
            activation = PostgresKillSwitch(DATABASE_URL).activate(
                KillActivation(
                    request_id="phase7-migration-recovery-activation",
                    expected_version=0,
                    trigger_kind="MANUAL",
                    actor_id="operator:migration-test",
                    reason_code="MANUAL_SAFETY_STOP",
                    reason="verify audited manual recovery",
                    observed_at=datetime.fromisoformat("2026-07-20T00:00:00+00:00"),
                    context_digest="1" * 64,
                )
            )
            assert activation.version == 1
            with psycopg.connect(DATABASE_URL) as connection:
                recovery_id = "2" * 64
                session_digest = "8" * 64
                csrf_digest = "9" * 64
                connection.execute(
                    "INSERT INTO local_operators(actor_id,argon2id_phc,created_at) "
                    "VALUES ('operator-local-1',"
                    "'$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$"
                    "aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA','2026-07-19T23:50:00Z')"
                )
                connection.execute(
                    "INSERT INTO operator_sessions"
                    "(session_digest,actor_id,issued_at,last_seen_at,idle_expires_at,"
                    "absolute_expires_at) VALUES (%s,'operator-local-1',%s,%s,%s,%s)",
                    (
                        session_digest,
                        "2026-07-19T23:50:00+00:00",
                        "2026-07-20T00:00:00+00:00",
                        "2026-07-20T00:20:00+00:00",
                        "2026-07-20T07:50:00+00:00",
                    ),
                )
                connection.execute(
                    "INSERT INTO session_csrf_tokens"
                    "(csrf_token_digest,session_digest,issued_at,expires_at,consumed_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        csrf_digest,
                        session_digest,
                        "2026-07-20T00:00:00+00:00",
                        "2026-07-20T00:10:00+00:00",
                        "2026-07-20T00:00:30+00:00",
                    ),
                )
                connection.execute(
                    "INSERT INTO kill_recovery_events"
                    "(recovery_event_id,scope,idempotency_key,request_hash,actor_id,"
                    "session_digest,csrf_token_digest,origin_hash,incident_reference,reason,"
                    "observed_at,context_digest,data_status,data_state_hash,"
                    "reconciliation_status,reconciliation_checkpoint_hash,ledger_status,"
                    "ledger_snapshot_hash,prior_version,new_version) "
                    "VALUES (%s,'paper-global','phase7-recovery-key',%s,'operator-local-1',"
                    "%s,%s,%s,'incident-7','operator verified healthy state',%s,%s,"
                    "'HEALTHY',%s,'PASS',%s,'BALANCED',%s,1,2)",
                    (
                        recovery_id,
                        "3" * 64,
                        session_digest,
                        csrf_digest,
                        "a" * 64,
                        "2026-07-20T00:01:00+00:00",
                        "4" * 64,
                        "5" * 64,
                        "6" * 64,
                        "7" * 64,
                    ),
                )
                connection.execute(
                    "INSERT INTO risk_kill_recovery_command_receipts"
                    "(idempotency_key,request_hash,recovery_event_id,response,created_at) "
                    "VALUES ('phase7-recovery-key',%s,%s,%s,%s)",
                    (
                        "3" * 64,
                        recovery_id,
                        Jsonb({"recovery_event_id": recovery_id, "version": 2}),
                        "2026-07-20T00:01:00+00:00",
                    ),
                )
                recovery_event_data = {
                    "recovery_event_id": recovery_id,
                    "scope": "paper-global",
                    "active": False,
                    "prior_version": 1,
                    "version": 2,
                    "actor_id": "operator-local-1",
                    "session_binding_hash": session_digest,
                    "csrf_binding_hash": csrf_digest,
                    "origin_hash": "a" * 64,
                    "incident_reference": "incident-7",
                    "reason": "operator verified healthy state",
                    "observed_at": "2026-07-20T00:01:00+00:00",
                    "context_digest": "4" * 64,
                    "data_status": "HEALTHY",
                    "data_state_hash": "5" * 64,
                    "reconciliation_status": "PASS",
                    "reconciliation_checkpoint_hash": "6" * 64,
                    "ledger_status": "BALANCED",
                    "ledger_snapshot_hash": "7" * 64,
                }
                recovery_outbox_id = "d" * 64
                connection.execute(
                    "WITH material AS (SELECT %s::jsonb AS data), hashed AS ("
                    "SELECT data,encode(digest(convert_to("
                    "risk_canonical_jsonb(data),'UTF8'),'sha256'),'hex') AS payload_hash "
                    "FROM material) INSERT INTO outbox_events("
                    "event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
                    "aggregate_id,aggregate_version) SELECT %s::varchar,"
                    "'kill-switch.recovered.v2',jsonb_build_object("
                    "'spec_version','woozoo.event/v1','event_id',%s::text,"
                    "'event_type','kill-switch.recovered.v2','event_version',2,"
                    "'occurred_at','2026-07-20T00:01:00+00:00','producer','risk-engine',"
                    "'activation_phase',7,'aggregate_id',%s::text,'aggregate_version',1,"
                    "'payload_hash',payload_hash,'data',data),payload_hash,"
                    "'2026-07-20T00:01:00+00:00','kill_recovery',%s::varchar,1 FROM hashed",
                    (
                        Jsonb(recovery_event_data),
                        recovery_outbox_id,
                        recovery_outbox_id,
                        recovery_id,
                        recovery_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
                    "VALUES (%s,'kill-recovery',%s)",
                    (recovery_outbox_id, recovery_id),
                )
                connection.execute(
                    "UPDATE kill_switch_state SET active=false,version=2,"
                    "last_recovery_event_id=%s WHERE scope='paper-global' AND version=1",
                    (recovery_id,),
                )
                connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
                assert connection.execute(
                    "SELECT active,version,last_recovery_event_id FROM kill_switch_state "
                    "WHERE scope='paper-global'"
                ).fetchone() == (False, 2, recovery_id)
                connection.rollback()
            run(sys.executable, "-m", "alembic", "downgrade", "20260720_0006")
            with psycopg.connect(DATABASE_URL) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                    "20260720_0006",
                )
                assert connection.execute(
                    "SELECT count(*) FROM pg_roles WHERE rolname='woozoo_control_api'"
                ).fetchone() == (0,)
                assert connection.execute(
                    "SELECT to_regclass('public.paper_execution_authorizations')"
                ).fetchone() == (None,)
                assert connection.execute(
                    "SELECT to_regprocedure("
                    "'public.paper_recorded_book_market_is_current_v1(character varying)')"
                ).fetchone() == (None,)
        finally:
            run("docker", "compose", "down", "-v")


def test_fresh_phase7_opening_account_reconciles_healthy() -> None:
    with docker_infrastructure_lock():
        run("docker", "compose", "down", "-v")
        run("docker", "compose", "up", "-d", "--wait", "postgres")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "20260720_0007")
            result = PostgresPaperStore(PAPER_WRITER_URL).reconcile(
                PAPER_ACCOUNT_ID,
                checkpoint_id="phase7-opening-reconciliation",
                created_at=datetime.fromisoformat("2026-07-20T00:00:01+00:00"),
            )
            assert result.status == "HEALTHY"
            assert result.mismatch_codes == ()
        finally:
            run("docker", "compose", "down", "-v")
