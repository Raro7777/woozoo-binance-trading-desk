"""Activate Phase 7 local auth and the human-approved Paper execution chain."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260720_0007"
down_revision = "20260720_0006"
branch_labels = None
depends_on = None


HASH_CHECK = " ~ '^[a-f0-9]{64}$'"
NONCE_CHECK = " ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{31,127}$'"
PAPER_DEFAULT_ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"
PAPER_BOOTSTRAP_TRANSACTION_ID = "1b0ed16915be7b75e206c111184b2c450eb3fe498ff46b95130c5cf1f5baaf4c"
PAPER_BOOTSTRAP_EVENT_ID = "40d71b52c06ddfb07e10cd2f2ce48e2214cb7b871ce921633d20deae660ea3bd"


def _append_only(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_risk_history_mutation()
    """)


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "ingestion_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_outbox_events_ingestion_sequence",
        "outbox_events",
        ["ingestion_sequence"],
    )
    op.add_column(
        "paper_reconciliation_checkpoints",
        sa.Column(
            "authority_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_table(
        "trading_room_migration_metadata",
        sa.Column("migration_revision", sa.String(32), primary_key=True),
        sa.Column("control_role_created", sa.Boolean(), nullable=False),
        sa.Column("control_schema_usage_preexisting", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "phase7_function_backup",
        sa.Column("function_name", sa.String(128), primary_key=True),
        sa.Column("function_definition", sa.Text(), nullable=False),
    )
    op.create_table(
        "paper_authorization_worker_state",
        sa.Column("worker_name", sa.String(64), primary_key=True),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(64), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "worker_name='phase7-paper-authorization'",
            name="ck_paper_worker_name",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING','FAILED','STOPPED')",
            name="ck_paper_worker_status",
        ),
        sa.CheckConstraint(
            "heartbeat_at>=started_at AND "
            "(last_progress_at IS NULL OR last_progress_at>=started_at) AND "
            "(stopped_at IS NULL OR stopped_at>=started_at)",
            name="ck_paper_worker_times",
        ),
        sa.CheckConstraint(
            "(status='RUNNING' AND stopped_at IS NULL) OR "
            "(status IN ('FAILED','STOPPED') AND stopped_at IS NOT NULL)",
            name="ck_paper_worker_terminal",
        ),
    )
    op.create_table(
        "agent_analysis_command_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_snapshots.evidence_id"]),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"]),
        sa.CheckConstraint(
            "request_hash ~ '^[a-f0-9]{64}$'", name="ck_agent_analysis_receipt_request_hash"
        ),
        sa.CheckConstraint(
            "evidence_id ~ '^[a-f0-9]{64}$'", name="ck_agent_analysis_receipt_evidence_id"
        ),
    )
    _append_only("agent_analysis_command_receipts")
    op.create_table(
        "trading_room_audit_projection",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "projected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.event_id"]),
    )
    _append_only("trading_room_audit_projection")
    op.execute("""
        INSERT INTO phase7_function_backup(function_name,function_definition)
        VALUES
          ('assert_kill_activation_consistency',
           pg_get_functiondef('assert_kill_activation_consistency()'::regprocedure)),
          ('enforce_phase6_risk_proposal',
           pg_get_functiondef('enforce_phase6_risk_proposal()'::regprocedure)),
          ('assert_paper_relational_consistency',
           pg_get_functiondef('assert_paper_relational_consistency()'::regprocedure))
    """)
    op.execute("""
        CREATE FUNCTION stamp_paper_reconciliation_authority_sequence_v1()
        RETURNS trigger AS $$
        BEGIN
          SELECT COALESCE(max(event.ingestion_sequence),0)
            INTO NEW.authority_sequence
          FROM paper_outbox_links link
          JOIN outbox_events event USING(event_id)
          WHERE link.account_id=NEW.account_id;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION stamp_paper_reconciliation_authority_sequence_v1() FROM PUBLIC"
    )
    op.execute("""
        CREATE TRIGGER paper_reconciliation_authority_sequence_v1
        BEFORE INSERT ON paper_reconciliation_checkpoints
        FOR EACH ROW EXECUTE FUNCTION stamp_paper_reconciliation_authority_sequence_v1()
    """)

    op.create_table(
        "local_operators",
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column("argon2id_phc", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("actor_id='operator-local-1'", name="ck_local_operator_actor"),
        sa.CheckConstraint(
            "argon2id_phc LIKE '$argon2id$%' AND length(argon2id_phc) BETWEEN 32 AND 512",
            name="ck_local_operator_argon2id",
        ),
    )
    op.create_table(
        "operator_sessions",
        sa.Column("session_digest", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("session_digest" + HASH_CHECK, name="ck_operator_session_digest"),
        sa.CheckConstraint(
            "issued_at<=last_seen_at AND idle_expires_at<=last_seen_at + interval '30 minutes' "
            "AND idle_expires_at<=absolute_expires_at AND "
            "absolute_expires_at<=issued_at + interval '8 hours'",
            name="ck_operator_session_expiry",
        ),
    )
    op.create_table(
        "session_csrf_tokens",
        sa.Column("csrf_token_digest", sa.String(64), primary_key=True),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("csrf_token_digest" + HASH_CHECK, name="ck_session_csrf_digest"),
        sa.CheckConstraint(
            "expires_at>issued_at AND expires_at<=issued_at + interval '10 minutes'",
            name="ck_session_csrf_expiry",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR (consumed_at>=issued_at AND consumed_at<=expires_at)",
            name="ck_session_csrf_consumed",
        ),
    )

    op.create_table(
        "paper_approvals",
        sa.Column("approval_id", sa.String(64), primary_key=True),
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("risk_decision_id", sa.String(64), nullable=False, unique=True),
        sa.Column("risk_decision_hash", sa.String(64), nullable=False),
        sa.Column("risk_input_digest", sa.String(64), nullable=False),
        sa.Column("risk_policy_version", sa.String(64), nullable=False),
        sa.Column("paper_order_preview", postgresql.JSONB(), nullable=False),
        sa.Column("paper_order_preview_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("approval_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("expected_kill_switch_version", sa.BigInteger(), nullable=False),
        sa.Column("expected_portfolio_version", sa.BigInteger(), nullable=False),
        sa.Column("expected_ledger_version", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["risk_decision_id"], ["risk_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"],
            ["session_csrf_tokens.csrf_token_digest"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "approval_id"
            + HASH_CHECK
            + " AND proposal_hash"
            + HASH_CHECK
            + " AND risk_decision_hash"
            + HASH_CHECK
            + " AND risk_input_digest"
            + HASH_CHECK
            + " AND paper_order_preview_hash"
            + HASH_CHECK
            + " AND origin_hash"
            + HASH_CHECK
            + " AND payload_hash"
            + HASH_CHECK,
            name="ck_paper_approval_hashes",
        ),
        sa.CheckConstraint("actor_id='operator-local-1'", name="ck_paper_approval_actor"),
        sa.CheckConstraint(
            "decision IN ('APPROVED','REJECTED')", name="ck_paper_approval_decision"
        ),
        sa.CheckConstraint("approval_nonce" + NONCE_CHECK, name="ck_paper_approval_nonce"),
        sa.CheckConstraint(
            "expected_kill_switch_version>=0 AND expected_portfolio_version>=0 "
            "AND expected_ledger_version>=0",
            name="ck_paper_approval_versions",
        ),
        sa.CheckConstraint(
            "expires_at>decided_at AND expires_at<=decided_at + interval '5 minutes'",
            name="ck_paper_approval_ttl",
        ),
    )
    op.create_table(
        "paper_approval_revocations",
        sa.Column("revocation_id", sa.String(64), primary_key=True),
        sa.Column("approval_id", sa.String(64), nullable=False, unique=True),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("revocation_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["paper_approvals.approval_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"],
            ["session_csrf_tokens.csrf_token_digest"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "revocation_id"
            + HASH_CHECK
            + " AND approval_hash"
            + HASH_CHECK
            + " AND payload_hash"
            + HASH_CHECK,
            name="ck_paper_revocation_hashes",
        ),
        sa.CheckConstraint("origin_hash" + HASH_CHECK, name="ck_paper_revocation_origin_hash"),
        sa.CheckConstraint("actor_id='operator-local-1'", name="ck_paper_revocation_actor"),
        sa.CheckConstraint("revocation_nonce" + NONCE_CHECK, name="ck_paper_revocation_nonce"),
        sa.CheckConstraint(
            "length(reason) BETWEEN 1 AND 512 AND expected_version>=1",
            name="ck_paper_revocation_reason_version",
        ),
    )
    op.create_table(
        "risk_approval_command_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(64), nullable=False, unique=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["paper_approvals.approval_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("request_hash" + HASH_CHECK, name="ck_risk_approval_receipt_hash"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_risk_approval_receipt_key",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(response)='object'", name="ck_risk_approval_receipt_response"
        ),
    )
    op.create_table(
        "risk_approval_revocation_command_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("revocation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["revocation_id"],
            ["paper_approval_revocations.revocation_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "request_hash" + HASH_CHECK,
            name="ck_risk_approval_revocation_receipt_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_risk_approval_revocation_receipt_key",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(response)='object'",
            name="ck_risk_approval_revocation_receipt_response",
        ),
    )
    op.create_table(
        "paper_cancel_command_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["paper_orders.order_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("order_id", "expected_version", name="uq_paper_cancel_order_version"),
        sa.CheckConstraint("request_hash" + HASH_CHECK, name="ck_paper_cancel_request_hash"),
        sa.CheckConstraint("expected_version>=1", name="ck_paper_cancel_expected_version"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_paper_cancel_idempotency_key",
        ),
        sa.CheckConstraint(
            "response=jsonb_build_object("
            "'result','ORDER_CANCELLED','order_id',order_id,'status','CANCELLED',"
            "'version',expected_version+1)",
            name="ck_paper_cancel_response",
        ),
    )
    op.create_table(
        "paper_execution_authorizations",
        sa.Column("authorization_id", sa.String(64), primary_key=True),
        sa.Column("namespace", sa.String(16), nullable=False),
        sa.Column("approval_id", sa.String(64), nullable=False, unique=True),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("approval_nonce_hash", sa.String(64), nullable=False),
        sa.Column("authorization_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("risk_decision_id", sa.String(64), nullable=False),
        sa.Column("risk_decision_hash", sa.String(64), nullable=False),
        sa.Column("risk_input_digest", sa.String(64), nullable=False),
        sa.Column("risk_policy_version", sa.String(64), nullable=False),
        sa.Column("paper_order_preview_hash", sa.String(64), nullable=False),
        sa.Column("authorization_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("current_data_state_hash", sa.String(64), nullable=False),
        sa.Column("current_data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_knowledge_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kill_switch_version", sa.BigInteger(), nullable=False),
        sa.Column("reconciliation_checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("ledger_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("paper_account_id", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["paper_approvals.approval_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["risk_decision_id"], ["risk_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["paper_account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("namespace='paper'", name="ck_execution_authorization_namespace"),
        sa.CheckConstraint(
            "authorization_id"
            + HASH_CHECK
            + " AND approval_hash"
            + HASH_CHECK
            + " AND approval_nonce_hash"
            + HASH_CHECK
            + " AND proposal_hash"
            + HASH_CHECK
            + " AND risk_decision_hash"
            + HASH_CHECK
            + " AND risk_input_digest"
            + HASH_CHECK
            + " AND paper_order_preview_hash"
            + HASH_CHECK
            + " AND authorization_input_digest"
            + HASH_CHECK
            + " AND current_data_state_hash"
            + HASH_CHECK
            + " AND reconciliation_checkpoint_hash"
            + HASH_CHECK
            + " AND ledger_snapshot_hash"
            + HASH_CHECK,
            name="ck_execution_authorization_hashes",
        ),
        sa.CheckConstraint(
            "authorization_nonce" + NONCE_CHECK, name="ck_execution_authorization_nonce"
        ),
        sa.CheckConstraint(
            "current_data_as_of<=current_knowledge_cutoff AND kill_switch_version>=0",
            name="ck_execution_authorization_current_state",
        ),
        sa.CheckConstraint(
            "expires_at>issued_at AND expires_at<=issued_at + interval '5 minutes'",
            name="ck_execution_authorization_ttl",
        ),
    )

    op.create_table(
        "kill_recovery_events",
        sa.Column("recovery_event_id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("incident_reference", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_digest", sa.String(64), nullable=False),
        sa.Column("data_status", sa.String(16), nullable=False),
        sa.Column("data_state_hash", sa.String(64), nullable=False),
        sa.Column("reconciliation_status", sa.String(16), nullable=False),
        sa.Column("reconciliation_checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("ledger_status", sa.String(16), nullable=False),
        sa.Column("ledger_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("prior_version", sa.BigInteger(), nullable=False),
        sa.Column("new_version", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("scope", "new_version", name="uq_kill_recovery_scope_version"),
        sa.CheckConstraint("scope='paper-global'", name="ck_kill_recovery_scope"),
        sa.CheckConstraint("actor_id='operator-local-1'", name="ck_kill_recovery_actor"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"],
            ["session_csrf_tokens.csrf_token_digest"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "data_status='HEALTHY' AND reconciliation_status='PASS' AND ledger_status='BALANCED'",
            name="ck_kill_recovery_health",
        ),
        sa.CheckConstraint(
            "length(incident_reference) BETWEEN 1 AND 128 AND length(reason) BETWEEN 1 AND 512",
            name="ck_kill_recovery_reason",
        ),
        sa.CheckConstraint("new_version=prior_version+1", name="ck_kill_recovery_version"),
        sa.CheckConstraint(
            "recovery_event_id"
            + HASH_CHECK
            + " AND request_hash"
            + HASH_CHECK
            + " AND context_digest"
            + HASH_CHECK
            + " AND data_state_hash"
            + HASH_CHECK
            + " AND reconciliation_checkpoint_hash"
            + HASH_CHECK
            + " AND ledger_snapshot_hash"
            + HASH_CHECK
            + " AND origin_hash"
            + HASH_CHECK,
            name="ck_kill_recovery_hashes",
        ),
    )
    op.create_table(
        "risk_kill_recovery_command_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("recovery_event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recovery_event_id"],
            ["kill_recovery_events.recovery_event_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("request_hash" + HASH_CHECK, name="ck_kill_recovery_receipt_hash"),
    )
    op.create_table(
        "paper_kill_cancel_completions",
        sa.Column("activation_event_id", sa.String(64), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("paper_account_id", sa.String(64), nullable=False),
        sa.Column("batch_count", sa.Integer(), nullable=False),
        sa.Column("cancelled_count", sa.Integer(), nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["activation_event_id"],
            ["paper_kill_inbox.activation_event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("payload_hash" + HASH_CHECK, name="ck_kill_completion_payload_hash"),
        sa.CheckConstraint("state_digest" + HASH_CHECK, name="ck_kill_completion_state_hash"),
        sa.CheckConstraint(
            "batch_count>=0 AND cancelled_count>=0", name="ck_kill_completion_counts"
        ),
    )

    for table in (
        "paper_approvals",
        "paper_approval_revocations",
        "risk_approval_command_receipts",
        "risk_approval_revocation_command_receipts",
        "paper_cancel_command_receipts",
        "paper_execution_authorizations",
        "kill_recovery_events",
        "risk_kill_recovery_command_receipts",
        "paper_kill_cancel_completions",
    ):
        _append_only(table)

    op.execute(r"""
        CREATE FUNCTION enforce_paper_kill_cancel_completion_v1() RETURNS trigger AS $$
        DECLARE inbox_row record; observed_batches integer; observed_items integer;
        BEGIN
          SELECT * INTO inbox_row FROM paper_kill_inbox
            WHERE activation_event_id=NEW.activation_event_id;
          SELECT count(*),COALESCE(sum(cancelled_count),0)
            INTO observed_batches,observed_items
            FROM paper_kill_cancel_batches
            WHERE activation_event_id=NEW.activation_event_id;
          IF NOT FOUND OR inbox_row.payload_hash<>NEW.payload_hash
             OR NEW.batch_count<>observed_batches OR NEW.cancelled_count<>observed_items
             OR observed_items<>(SELECT count(*) FROM paper_kill_cancel_items
                                  WHERE activation_event_id=NEW.activation_event_id)
             OR EXISTS (SELECT 1 FROM paper_orders
                        WHERE status IN ('OPEN','PARTIALLY_FILLED'))
             OR EXISTS (SELECT 1 FROM paper_kill_cancel_batches
                        WHERE activation_event_id=NEW.activation_event_id
                          AND completed_at>NEW.completed_at)
          THEN RAISE EXCEPTION 'PAPER_KILL_CANCELLATION_NOT_COMPLETE'; END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_paper_kill_cancel_completion_v1() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_kill_cancel_completion_binding
        AFTER INSERT ON paper_kill_cancel_completions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_paper_kill_cancel_completion_v1()
    """)

    op.execute(r"""
        CREATE FUNCTION enforce_risk_approval_receipt() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM paper_approvals approval
            WHERE approval.approval_id=NEW.approval_id
              AND NEW.created_at=approval.decided_at
              AND NEW.response->>'result'=approval.decision
              AND NEW.response->'approval'->>'approval_id'=approval.approval_id
              AND NEW.response->'approval'->>'payload_hash'=approval.payload_hash
              AND NEW.response->'approval'->>'decision'=approval.decision)
          THEN
            RAISE EXCEPTION 'Risk approval receipt is not bound to its approval';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_risk_approval_receipt() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER risk_approval_receipt_binding
        AFTER INSERT ON risk_approval_command_receipts DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_risk_approval_receipt()
    """)
    op.execute(r"""
        CREATE FUNCTION enforce_risk_approval_revocation_receipt() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM paper_approval_revocations revocation
            WHERE revocation.revocation_id=NEW.revocation_id
              AND NEW.created_at=revocation.revoked_at
              AND NEW.response->>'result'='REVOKED'
              AND NEW.response->'revocation'->>'revocation_id'=revocation.revocation_id
              AND NEW.response->'revocation'->>'approval_id'=revocation.approval_id
              AND NEW.response->'revocation'->>'payload_hash'=revocation.payload_hash)
          THEN
            RAISE EXCEPTION 'Risk approval revocation receipt is not bound to its revocation';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_risk_approval_revocation_receipt() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER risk_approval_revocation_receipt_binding
        AFTER INSERT ON risk_approval_revocation_command_receipts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_risk_approval_revocation_receipt()
    """)

    op.execute("ALTER TABLE paper_accounts DROP CONSTRAINT ck_p4_paper_account_test_only")
    op.create_check_constraint(
        "ck_phase7_paper_account_namespace", "paper_accounts", "namespace IN ('test','paper')"
    )
    op.execute(
        "ALTER TABLE paper_authorization_attempts DROP CONSTRAINT ck_p4_authorization_test_only"
    )
    op.create_check_constraint(
        "ck_phase7_authorization_namespace",
        "paper_authorization_attempts",
        "namespace IN ('test','paper')",
    )
    op.add_column(
        "paper_authorization_attempts",
        sa.Column("paper_execution_authorization_id", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_paper_attempt_phase7_authorization",
        "paper_authorization_attempts",
        "paper_execution_authorizations",
        ["paper_execution_authorization_id"],
        ["authorization_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_phase7_attempt_authorization_link",
        "paper_authorization_attempts",
        "(namespace='test' AND paper_execution_authorization_id IS NULL) OR "
        "(namespace='paper' AND paper_execution_authorization_id=authorization_id)",
    )
    op.execute("ALTER TABLE paper_broker_inputs DROP CONSTRAINT ck_p4_broker_input_kind")
    op.execute("ALTER TABLE paper_broker_inputs DROP CONSTRAINT ck_paper_input_liquidity")
    op.add_column(
        "paper_broker_inputs",
        sa.Column("paper_execution_authorization_id", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_paper_input_phase7_authorization",
        "paper_broker_inputs",
        "paper_execution_authorizations",
        ["paper_execution_authorization_id"],
        ["authorization_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_phase7_broker_input_kind",
        "paper_broker_inputs",
        "source_kind IN ('TEST_COMMAND','RECORDED_BOOK','PAPER_AUTHORIZATION')",
    )
    op.create_check_constraint(
        "ck_phase7_broker_input_liquidity",
        "paper_broker_inputs",
        "(source_kind='TEST_COMMAND' AND paper_execution_authorization_id IS NULL "
        "AND available_quantity IS NULL AND symbol IS NULL AND best_bid IS NULL "
        "AND best_ask IS NULL) OR "
        "(source_kind='PAPER_AUTHORIZATION' AND paper_execution_authorization_id IS NOT NULL "
        "AND available_quantity IS NULL AND symbol IS NULL AND best_bid IS NULL "
        "AND best_ask IS NULL) OR "
        "(source_kind='RECORDED_BOOK' AND paper_execution_authorization_id IS NULL "
        "AND available_quantity>0 AND symbol IN ('BTCUSDT','ETHUSDT') "
        "AND best_bid>0 AND best_ask>0 AND best_bid<=best_ask)",
    )
    op.execute("ALTER TABLE analysis_runs DROP CONSTRAINT ck_analysis_run_namespace")
    op.create_check_constraint(
        "ck_phase7_analysis_run_namespace", "analysis_runs", "namespace IN ('test','paper')"
    )
    op.drop_constraint("ck_risk_outbox_kind", "risk_outbox_links", type_="check")
    op.create_check_constraint(
        "ck_phase7_risk_outbox_kind",
        "risk_outbox_links",
        "aggregate_kind IN ('risk-decision','kill-switch','paper-approval',"
        "'paper-approval-revocation','paper-authorization','kill-recovery')",
    )

    op.execute("""
        CREATE FUNCTION enforce_phase7_approval_binding() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM trade_proposals proposal
            JOIN risk_decisions decision ON decision.decision_id=NEW.risk_decision_id
            JOIN operator_sessions session ON session.session_digest=NEW.session_digest
            JOIN session_csrf_tokens csrf ON csrf.csrf_token_digest=NEW.csrf_token_digest
            WHERE proposal.proposal_id=NEW.proposal_id
              AND proposal.proposal_hash=NEW.proposal_hash
              AND decision.proposal_id=proposal.proposal_id
              AND decision.proposal_hash=proposal.proposal_hash
              AND decision.decision_hash=NEW.risk_decision_hash
              AND decision.risk_input_digest=NEW.risk_input_digest
              AND decision.policy_version=NEW.risk_policy_version
              AND decision.paper_order_preview_hash=NEW.paper_order_preview_hash
              AND decision.risk_input->'order_preview'=NEW.paper_order_preview
              AND decision.risk_input->>'risk_input_schema_version'='woozoo.risk-input/v3'
              AND decision.risk_input->>'namespace'='paper'
              AND decision.kill_switch_version=NEW.expected_kill_switch_version
              AND (NEW.decision='REJECTED' OR decision.verdict='ALLOWED')
              AND session.actor_id=NEW.actor_id
              AND session.issued_at<=NEW.decided_at
              AND session.last_seen_at<=NEW.decided_at
              AND session.idle_expires_at>=NEW.decided_at
              AND session.absolute_expires_at>=NEW.decided_at
              AND (session.revoked_at IS NULL OR session.revoked_at>NEW.decided_at)
              AND csrf.session_digest=session.session_digest
              AND csrf.consumed_at IS NOT NULL
              AND csrf.consumed_at<=NEW.decided_at
              AND csrf.expires_at>=NEW.decided_at
          ) THEN
            RAISE EXCEPTION 'Paper approval session, CSRF, Proposal, or Risk binding is incomplete';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_phase7_approval_binding() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_approval_phase7_binding
        AFTER INSERT ON paper_approvals DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase7_approval_binding()
    """)
    op.execute("""
        CREATE FUNCTION enforce_phase7_revocation_binding() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM paper_approvals approval
            JOIN operator_sessions session ON session.session_digest=NEW.session_digest
            JOIN session_csrf_tokens csrf ON csrf.csrf_token_digest=NEW.csrf_token_digest
            WHERE approval.approval_id=NEW.approval_id
              AND approval.payload_hash=NEW.approval_hash
              AND approval.decision='APPROVED'
              AND NEW.expected_version=1
              AND session.actor_id=NEW.actor_id
              AND session.issued_at<=NEW.revoked_at
              AND session.last_seen_at<=NEW.revoked_at
              AND session.idle_expires_at>=NEW.revoked_at
              AND session.absolute_expires_at>=NEW.revoked_at
              AND (session.revoked_at IS NULL OR session.revoked_at>NEW.revoked_at)
              AND csrf.session_digest=session.session_digest
              AND csrf.consumed_at IS NOT NULL
              AND csrf.consumed_at<=NEW.revoked_at
              AND csrf.expires_at>=NEW.revoked_at
          ) THEN
            RAISE EXCEPTION 'Paper approval revocation session, CSRF, or approval hash binding is incomplete';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_phase7_revocation_binding() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_approval_revocation_phase7_binding
        AFTER INSERT ON paper_approval_revocations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase7_revocation_binding()
    """)
    op.execute("""
        CREATE FUNCTION enforce_phase7_recovery_actor_binding() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM operator_sessions session
            JOIN session_csrf_tokens csrf ON csrf.session_digest=session.session_digest
            WHERE session.session_digest=NEW.session_digest
              AND csrf.csrf_token_digest=NEW.csrf_token_digest
              AND session.actor_id=NEW.actor_id
              AND session.issued_at<=NEW.observed_at
              AND session.last_seen_at<=NEW.observed_at
              AND session.idle_expires_at>=NEW.observed_at
              AND session.absolute_expires_at>=NEW.observed_at
              AND (session.revoked_at IS NULL OR session.revoked_at>NEW.observed_at)
              AND csrf.consumed_at IS NOT NULL
              AND csrf.consumed_at<=NEW.observed_at
              AND csrf.expires_at>=NEW.observed_at
          ) THEN
            RAISE EXCEPTION 'Kill recovery session or CSRF binding is incomplete';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_phase7_recovery_actor_binding() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER kill_recovery_actor_phase7_binding
        AFTER INSERT ON kill_recovery_events DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase7_recovery_actor_binding()
    """)

    op.execute("""
        CREATE FUNCTION enforce_phase7_authorization_binding() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM paper_approvals approval
            JOIN risk_decisions decision ON decision.decision_id=approval.risk_decision_id
            JOIN paper_accounts account ON account.account_id=NEW.paper_account_id
            WHERE approval.approval_id=NEW.approval_id
              AND approval.decision='APPROVED'
              AND approval.payload_hash=NEW.approval_hash
              AND NOT EXISTS (
                SELECT 1 FROM paper_approval_revocations revocation
                WHERE revocation.approval_id=approval.approval_id)
              AND approval.proposal_id=NEW.proposal_id
              AND approval.proposal_hash=NEW.proposal_hash
              AND approval.risk_decision_id=NEW.risk_decision_id
              AND approval.risk_decision_hash=NEW.risk_decision_hash
              AND approval.risk_input_digest=NEW.risk_input_digest
              AND approval.risk_policy_version=NEW.risk_policy_version
              AND approval.paper_order_preview_hash=NEW.paper_order_preview_hash
              AND approval.expected_kill_switch_version=NEW.kill_switch_version
              AND encode(digest(convert_to(approval.approval_nonce,'UTF8'),'sha256'),'hex')
                    =NEW.approval_nonce_hash
              AND approval.approval_nonce<>NEW.authorization_nonce
              AND approval.decided_at<=NEW.issued_at
              AND NEW.expires_at<=approval.expires_at
              AND decision.verdict='ALLOWED'
              AND decision.decision_hash=NEW.risk_decision_hash
              AND decision.risk_input_digest=NEW.risk_input_digest
              AND decision.policy_version=NEW.risk_policy_version
              AND decision.proposal_hash=NEW.proposal_hash
              AND decision.paper_order_preview_hash=NEW.paper_order_preview_hash
              AND account.namespace='paper'
          ) THEN
            RAISE EXCEPTION 'Paper execution authorization binding is incomplete';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_phase7_authorization_binding() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_execution_authorization_binding
        AFTER INSERT ON paper_execution_authorizations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase7_authorization_binding()
    """)
    op.execute(f"""
        CREATE FUNCTION issue_paper_approval_v1(
          p_idempotency_key varchar,p_request_hash varchar,p_proposal_id varchar,
          p_decision varchar,p_expected_version bigint,p_preview_hash varchar,
          p_actor_id varchar,p_session_digest varchar,p_csrf_digest varchar,
          p_origin_hash varchar,p_approval_nonce varchar,p_authorization_nonce varchar,
          p_decided_at timestamptz)
        RETURNS TABLE(created boolean,response jsonb) AS $$
        DECLARE
          prior record; proposal_row record; decision_row record; kill_row record;
          checkpoint_row record; portfolio_version bigint; ledger_version bigint;
          approval_unsigned jsonb; approval_payload jsonb; approval_hash varchar;
          approval_id varchar; authorization_payload jsonb; authorization_id varchar;
          authorization_input_digest varchar; expires_at timestamptz;
          event_data jsonb; event_payload jsonb; event_id varchar; event_payload_hash varchar;
        BEGIN
          IF length(p_idempotency_key) NOT BETWEEN 1 AND 128
             OR p_request_hash !~ '^[a-f0-9]{{64}}$'
             OR p_expected_version<>1
             OR p_decision NOT IN ('APPROVED','REJECTED')
             OR p_approval_nonce=p_authorization_nonce
          THEN RAISE EXCEPTION 'APPROVAL_COMMAND_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'approval:'||p_idempotency_key,0));
          SELECT receipt.request_hash,receipt.response INTO prior
            FROM risk_approval_command_receipts AS receipt
            WHERE receipt.idempotency_key=p_idempotency_key;
          IF FOUND THEN
            IF prior.request_hash<>p_request_hash THEN
              RAISE EXCEPTION 'APPROVAL_IDEMPOTENCY_CONFLICT';
            END IF;
            RETURN QUERY SELECT false,prior.response;
            RETURN;
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'paper-account:{PAPER_DEFAULT_ACCOUNT_ID}',0));
          SELECT * INTO proposal_row FROM trade_proposals
            WHERE proposal_id=p_proposal_id FOR SHARE;
          IF NOT FOUND OR proposal_row.risk_eligible IS NOT true
             OR proposal_row.side NOT IN ('BUY','SELL') THEN
            RAISE EXCEPTION 'PROPOSAL_NOT_RISK_ELIGIBLE';
          END IF;
          SELECT * INTO decision_row FROM risk_decisions
            WHERE proposal_id=p_proposal_id ORDER BY recorded_at DESC,decision_id DESC LIMIT 1;
          IF NOT FOUND OR decision_row.risk_input->>'risk_input_schema_version'<>
                'woozoo.risk-input/v3'
             OR decision_row.risk_input->>'namespace'<>'paper'
             OR decision_row.paper_order_preview_hash<>p_preview_hash
             OR decision_row.proposal_hash<>proposal_row.proposal_hash
             OR decision_row.risk_input->'proposal'->'payload'<>proposal_row.payload
             OR (p_decision='APPROVED' AND decision_row.verdict<>'ALLOWED')
             OR p_decided_at<decision_row.decision_as_of
             OR p_decided_at>decision_row.decision_as_of+interval '5 minutes'
          THEN RAISE EXCEPTION 'RISK_DECISION_DRIFT'; END IF;
          SELECT * INTO kill_row FROM kill_switch_state
            WHERE scope='paper-global' FOR UPDATE;
          IF NOT FOUND OR kill_row.version<>decision_row.kill_switch_version
             OR (p_decision='APPROVED' AND kill_row.active)
          THEN RAISE EXCEPTION 'KILL_SWITCH_DRIFT'; END IF;
          SELECT * INTO checkpoint_row FROM paper_reconciliation_checkpoints
            WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
            ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1;
          IF NOT FOUND OR checkpoint_row.status<>'HEALTHY'
             OR checkpoint_row.mismatch_codes<>'[]'::jsonb
             OR encode(digest(convert_to(risk_canonical_jsonb(jsonb_build_object(
                  'checkpoint_id',checkpoint_row.checkpoint_id,
                  'health',checkpoint_row.status,
                  'mismatch_codes',checkpoint_row.mismatch_codes)),'UTF8'),'sha256'),'hex')
                <>decision_row.reconciliation_checkpoint_hash
          THEN RAISE EXCEPTION 'RECONCILIATION_DRIFT'; END IF;
          SELECT COALESCE(max(version),0) INTO portfolio_version
            FROM paper_asset_balances WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}';
          SELECT count(*) INTO ledger_version FROM paper_ledger_transactions
            WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}';
          expires_at:=p_decided_at+interval '5 minutes';
          approval_unsigned:=jsonb_build_object(
            'approval_id',NULL,'proposal_id',proposal_row.proposal_id,
            'proposal_hash',proposal_row.proposal_hash,
            'risk_decision_id',decision_row.decision_id,
            'risk_decision_hash',decision_row.decision_hash,
            'risk_input_digest',decision_row.risk_input_digest,
            'risk_policy_version',decision_row.policy_version,
            'paper_order_preview',decision_row.risk_input->'order_preview',
            'paper_order_preview_hash',decision_row.paper_order_preview_hash,
            'actor_id',p_actor_id,'session_binding_hash',p_session_digest,
            'csrf_binding_hash',p_csrf_digest,'origin_hash',p_origin_hash,
            'decision',p_decision,'approval_nonce',p_approval_nonce,
            'expected_kill_switch_version',kill_row.version,
            'expected_portfolio_version',portfolio_version,
            'expected_ledger_version',ledger_version,
            'decided_at',to_jsonb(p_decided_at),'expires_at',to_jsonb(expires_at));
          approval_unsigned:=jsonb_set(approval_unsigned,'{{approval_id}}',
            to_jsonb(encode(digest(convert_to(risk_canonical_jsonb(
              jsonb_build_array('paper-approval',approval_unsigned-'approval_id')),
              'UTF8'),'sha256'),'hex')));
          approval_id:=approval_unsigned->>'approval_id';
          approval_hash:=encode(digest(convert_to(risk_canonical_jsonb(approval_unsigned),
            'UTF8'),'sha256'),'hex');
          approval_payload:=approval_unsigned||jsonb_build_object('payload_hash',approval_hash);
          INSERT INTO paper_approvals(
            approval_id,proposal_id,proposal_hash,risk_decision_id,risk_decision_hash,
            risk_input_digest,risk_policy_version,paper_order_preview,
            paper_order_preview_hash,actor_id,session_digest,csrf_token_digest,origin_hash,
            decision,approval_nonce,expected_kill_switch_version,
            expected_portfolio_version,expected_ledger_version,decided_at,expires_at,payload_hash)
          VALUES (approval_id,proposal_row.proposal_id,proposal_row.proposal_hash,
            decision_row.decision_id,decision_row.decision_hash,decision_row.risk_input_digest,
            decision_row.policy_version,decision_row.risk_input->'order_preview',
            decision_row.paper_order_preview_hash,p_actor_id,p_session_digest,p_csrf_digest,
            p_origin_hash,p_decision,p_approval_nonce,kill_row.version,portfolio_version,
            ledger_version,p_decided_at,expires_at,approval_hash);
          event_data:=approval_payload;
          event_payload_hash:=encode(digest(convert_to(risk_canonical_jsonb(event_data),
            'UTF8'),'sha256'),'hex');
          event_id:=encode(digest(convert_to(risk_canonical_jsonb(jsonb_build_array(
            'event','paper.approval.recorded.v1',approval_id,'1')),
            'UTF8'),'sha256'),'hex');
          event_payload:=jsonb_build_object(
            'spec_version','woozoo.event/v1','event_id',event_id,
            'event_type','paper.approval.recorded.v1','event_version',2,
            'occurred_at',to_jsonb(p_decided_at),'producer','risk-engine',
            'activation_phase',7,'aggregate_id',approval_id,'aggregate_version',1,
            'payload_hash',event_payload_hash,'data',event_data);
          INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,occurred_at,
            aggregate_type,aggregate_id,aggregate_version)
          VALUES (event_id,'paper.approval.recorded.v1',event_payload,event_payload_hash,
            p_decided_at,'paper_approval',approval_id,1);
          INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id)
          VALUES (event_id,'paper-approval',approval_id);
          IF p_decision='APPROVED' THEN
            authorization_input_digest:=encode(digest(convert_to(risk_canonical_jsonb(
              jsonb_build_object('approval_hash',approval_hash,
                'current_data_state_hash',decision_row.data_state_hash,
                'kill_switch_version',kill_row.version,
                'ledger_snapshot_hash',checkpoint_row.input_digest,
                'paper_order_preview_hash',decision_row.paper_order_preview_hash,
                'reconciliation_checkpoint_hash',decision_row.reconciliation_checkpoint_hash)),
              'UTF8'),'sha256'),'hex');
            authorization_id:=encode(digest(convert_to(risk_canonical_jsonb(
              jsonb_build_array('paper-authorization',authorization_input_digest)),
              'UTF8'),'sha256'),'hex');
            authorization_payload:=jsonb_build_object(
              'authorization_id',authorization_id,'namespace','paper',
              'approval_id',approval_id,'approval_hash',approval_hash,
              'approval_nonce_hash',encode(digest(convert_to(p_approval_nonce,'UTF8'),'sha256'),'hex'),
              'authorization_nonce',p_authorization_nonce,
              'proposal_id',proposal_row.proposal_id,'proposal_hash',proposal_row.proposal_hash,
              'risk_decision_id',decision_row.decision_id,
              'risk_decision_hash',decision_row.decision_hash,
              'risk_input_digest',decision_row.risk_input_digest,
              'risk_policy_version',decision_row.policy_version,
              'paper_order_preview_hash',decision_row.paper_order_preview_hash,
              'authorization_input_digest',authorization_input_digest,
              'current_data_state_hash',decision_row.data_state_hash,
              'current_data_as_of',decision_row.risk_input->'data'->'as_of',
              'current_knowledge_cutoff',decision_row.risk_input->'data'->'knowledge_cutoff',
              'kill_switch_version',kill_row.version,
              'reconciliation_checkpoint_hash',decision_row.reconciliation_checkpoint_hash,
              'ledger_snapshot_hash',checkpoint_row.input_digest,
              'paper_account_id','{PAPER_DEFAULT_ACCOUNT_ID}',
              'issued_at',to_jsonb(p_decided_at),'expires_at',to_jsonb(expires_at));
            INSERT INTO paper_execution_authorizations(
              authorization_id,namespace,approval_id,approval_hash,approval_nonce_hash,
              authorization_nonce,proposal_id,proposal_hash,risk_decision_id,
              risk_decision_hash,risk_input_digest,risk_policy_version,
              paper_order_preview_hash,authorization_input_digest,current_data_state_hash,
              current_data_as_of,current_knowledge_cutoff,kill_switch_version,
              reconciliation_checkpoint_hash,ledger_snapshot_hash,paper_account_id,
              issued_at,expires_at)
            VALUES (authorization_id,'paper',approval_id,approval_hash,
              authorization_payload->>'approval_nonce_hash',p_authorization_nonce,
              proposal_row.proposal_id,proposal_row.proposal_hash,decision_row.decision_id,
              decision_row.decision_hash,decision_row.risk_input_digest,decision_row.policy_version,
              decision_row.paper_order_preview_hash,authorization_input_digest,
              decision_row.data_state_hash,
              (decision_row.risk_input->'data'->>'as_of')::timestamptz,
              (decision_row.risk_input->'data'->>'knowledge_cutoff')::timestamptz,
              kill_row.version,decision_row.reconciliation_checkpoint_hash,
              checkpoint_row.input_digest,'{PAPER_DEFAULT_ACCOUNT_ID}',p_decided_at,expires_at);
            event_data:=authorization_payload;
            event_payload_hash:=encode(digest(convert_to(risk_canonical_jsonb(event_data),
              'UTF8'),'sha256'),'hex');
            event_id:=encode(digest(convert_to(risk_canonical_jsonb(jsonb_build_array(
              'event','paper.authorization.issued.v1',authorization_id,'1')),
              'UTF8'),'sha256'),'hex');
            event_payload:=jsonb_build_object(
              'spec_version','woozoo.event/v1','event_id',event_id,
              'event_type','paper.authorization.issued.v1','event_version',2,
              'occurred_at',to_jsonb(p_decided_at),'producer','risk-engine',
              'activation_phase',7,'aggregate_id',authorization_id,'aggregate_version',1,
              'payload_hash',event_payload_hash,'data',event_data);
            INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,occurred_at,
              aggregate_type,aggregate_id,aggregate_version)
            VALUES (event_id,'paper.authorization.issued.v1',event_payload,event_payload_hash,
              p_decided_at,'paper_authorization',authorization_id,1);
            INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id)
            VALUES (event_id,'paper-authorization',authorization_id);
          END IF;
          response:=jsonb_build_object('result',p_decision,'approval',approval_payload);
          IF authorization_payload IS NOT NULL THEN
            response:=response||jsonb_build_object('authorization',authorization_payload);
          END IF;
          INSERT INTO risk_approval_command_receipts(
            idempotency_key,request_hash,approval_id,response,created_at)
          VALUES (p_idempotency_key,p_request_hash,approval_id,response,p_decided_at);
          RETURN QUERY SELECT true,response;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION issue_paper_approval_v1(varchar,varchar,varchar,varchar,"
        "bigint,varchar,varchar,varchar,varchar,varchar,varchar,varchar,timestamptz) FROM PUBLIC"
    )
    op.execute(r"""
        CREATE FUNCTION revoke_paper_approval_v1(
          p_idempotency_key varchar,p_request_hash varchar,p_approval_id varchar,
          p_expected_version bigint,p_reason varchar,p_actor_id varchar,
          p_session_digest varchar,p_csrf_digest varchar,p_origin_hash varchar,
          p_revocation_nonce varchar,p_revoked_at timestamptz)
        RETURNS TABLE(created boolean,response jsonb) AS $$
        DECLARE
          prior record; approval_row record; authorization_row record; revocation_unsigned jsonb;
          revocation_payload jsonb; revocation_id varchar; payload_hash varchar;
          event_payload jsonb; event_id varchar; event_payload_hash varchar;
        BEGIN
          IF length(p_idempotency_key) NOT BETWEEN 1 AND 128
             OR p_request_hash !~ '^[a-f0-9]{64}$' OR p_expected_version<>1
             OR length(p_reason) NOT BETWEEN 1 AND 512
          THEN RAISE EXCEPTION 'REVOCATION_COMMAND_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'approval-revocation:'||p_idempotency_key,0));
          SELECT receipt.request_hash,receipt.response INTO prior
            FROM risk_approval_revocation_command_receipts AS receipt
            WHERE receipt.idempotency_key=p_idempotency_key;
          IF FOUND THEN
            IF prior.request_hash<>p_request_hash THEN
              RAISE EXCEPTION 'REVOCATION_IDEMPOTENCY_CONFLICT';
            END IF;
            RETURN QUERY SELECT false,prior.response;
            RETURN;
          END IF;
          SELECT authorization_id INTO authorization_row
            FROM paper_execution_authorizations WHERE approval_id=p_approval_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'APPROVAL_NOT_REVOCABLE'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'paper-approval:'||p_approval_id,0));
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'paper-authorization:'||authorization_row.authorization_id,0));
          SELECT * INTO approval_row FROM paper_approvals
            WHERE approval_id=p_approval_id FOR UPDATE;
          SELECT * INTO authorization_row FROM paper_execution_authorizations
            WHERE approval_id=p_approval_id FOR UPDATE;
          IF NOT FOUND OR approval_row.decision<>'APPROVED'
             OR EXISTS (SELECT 1 FROM paper_approval_revocations
                        WHERE approval_id=p_approval_id)
             OR EXISTS (SELECT 1 FROM paper_execution_authorizations authz
                        JOIN paper_authorization_attempts attempt
                          ON attempt.paper_execution_authorization_id=authz.authorization_id
                        WHERE authz.approval_id=p_approval_id)
          THEN RAISE EXCEPTION 'APPROVAL_NOT_REVOCABLE'; END IF;
          revocation_unsigned:=jsonb_build_object(
            'revocation_id',NULL,'approval_id',approval_row.approval_id,
            'approval_hash',approval_row.payload_hash,'actor_id',p_actor_id,
            'session_binding_hash',p_session_digest,'csrf_binding_hash',p_csrf_digest,
            'origin_hash',p_origin_hash,'revocation_nonce',p_revocation_nonce,
            'reason',p_reason,'expected_version',p_expected_version,
            'revoked_at',to_jsonb(p_revoked_at));
          revocation_unsigned:=jsonb_set(revocation_unsigned,'{revocation_id}',
            to_jsonb(encode(digest(convert_to(risk_canonical_jsonb(
              jsonb_build_array('paper-approval-revocation',
                revocation_unsigned-'revocation_id')),'UTF8'),'sha256'),'hex')));
          revocation_id:=revocation_unsigned->>'revocation_id';
          payload_hash:=encode(digest(convert_to(risk_canonical_jsonb(revocation_unsigned),
            'UTF8'),'sha256'),'hex');
          revocation_payload:=revocation_unsigned||jsonb_build_object(
            'payload_hash',payload_hash);
          INSERT INTO paper_approval_revocations(
            revocation_id,approval_id,approval_hash,actor_id,session_digest,
            csrf_token_digest,origin_hash,revocation_nonce,reason,expected_version,
            revoked_at,payload_hash)
          VALUES (revocation_id,approval_row.approval_id,approval_row.payload_hash,p_actor_id,
            p_session_digest,p_csrf_digest,p_origin_hash,p_revocation_nonce,p_reason,
            p_expected_version,p_revoked_at,payload_hash);
          event_payload_hash:=encode(digest(convert_to(
            risk_canonical_jsonb(revocation_payload),'UTF8'),'sha256'),'hex');
          event_id:=encode(digest(convert_to(risk_canonical_jsonb(jsonb_build_array(
            'event','paper.approval.revoked.v1',revocation_id,'1')),
            'UTF8'),'sha256'),'hex');
          event_payload:=jsonb_build_object(
            'spec_version','woozoo.event/v1','event_id',event_id,
            'event_type','paper.approval.revoked.v1','event_version',2,
            'occurred_at',to_jsonb(p_revoked_at),'producer','risk-engine',
            'activation_phase',7,'aggregate_id',revocation_id,'aggregate_version',1,
            'payload_hash',event_payload_hash,'data',revocation_payload);
          INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,occurred_at,
            aggregate_type,aggregate_id,aggregate_version)
          VALUES (event_id,'paper.approval.revoked.v1',event_payload,event_payload_hash,
            p_revoked_at,'paper_approval_revocation',revocation_id,1);
          INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id)
          VALUES (event_id,'paper-approval-revocation',revocation_id);
          response:=jsonb_build_object(
            'result','REVOKED','revocation',revocation_payload);
          INSERT INTO risk_approval_revocation_command_receipts(
            idempotency_key,request_hash,revocation_id,response,created_at)
          VALUES (p_idempotency_key,p_request_hash,revocation_id,response,p_revoked_at);
          RETURN QUERY SELECT true,response;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION revoke_paper_approval_v1(varchar,varchar,varchar,bigint,"
        "varchar,varchar,varchar,varchar,varchar,varchar,timestamptz) FROM PUBLIC"
    )
    op.execute(r"""
        CREATE FUNCTION paper_lock_execution_authorization_v1(p_authorization_id varchar)
        RETURNS TABLE(
          authorization_nonce varchar,paper_account_id varchar,
          authorization_input_digest varchar,kill_switch_version bigint,
          reconciliation_checkpoint_hash varchar,ledger_snapshot_hash varchar,
          expires_at timestamptz,approval_id varchar,proposal_hash varchar,
          risk_decision_hash varchar,paper_order_preview jsonb,
          paper_order_preview_hash varchar,current_data_state_hash varchar,
          current_data_as_of timestamptz,current_knowledge_cutoff timestamptz,
          risk_market_books jsonb
        ) AS $$
          SELECT authz.authorization_nonce,authz.paper_account_id,
            authz.authorization_input_digest,authz.kill_switch_version,
            authz.reconciliation_checkpoint_hash,authz.ledger_snapshot_hash,
            authz.expires_at,authz.approval_id,authz.proposal_hash,
            authz.risk_decision_hash,approval.paper_order_preview,
            approval.paper_order_preview_hash,authz.current_data_state_hash,
            authz.current_data_as_of,authz.current_knowledge_cutoff,
            decision.risk_input->'market_books'
          FROM paper_execution_authorizations authz JOIN paper_approvals approval
            ON approval.approval_id=authz.approval_id
          JOIN risk_decisions decision
            ON decision.decision_id=authz.risk_decision_id
           AND decision.decision_hash=authz.risk_decision_hash
           AND decision.risk_input_digest=authz.risk_input_digest
          WHERE authz.authorization_id=p_authorization_id AND authz.namespace='paper'
            AND jsonb_typeof(decision.risk_input->'market_books')='object'
            AND decision.risk_input->'market_books' ?& array['BTCUSDT','ETHUSDT']
            AND (decision.risk_input->'market_books')
              - array['BTCUSDT','ETHUSDT']='{}'::jsonb
          FOR KEY SHARE OF authz,approval,decision
        $$ LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION paper_lock_execution_authorization_v1(varchar) FROM PUBLIC")
    op.execute(r"""
        CREATE FUNCTION paper_validate_kill_activation_v1(
          p_activation_event_id varchar,p_payload_hash varchar
        ) RETURNS boolean AS $$
          SELECT EXISTS (
            SELECT 1 FROM kill_switch_events kill_event
            JOIN risk_outbox_links link
              ON link.aggregate_id=kill_event.activation_event_id
             AND link.aggregate_kind='kill-switch'
            JOIN outbox_events outbox ON outbox.event_id=link.event_id
            WHERE kill_event.activation_event_id=p_activation_event_id
              AND outbox.payload_hash=p_payload_hash)
        $$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION paper_validate_kill_activation_v1(varchar,varchar) FROM PUBLIC"
    )
    op.execute(r"""
        CREATE FUNCTION paper_recorded_book_market_is_current_v1(p_market_event_id varchar)
        RETURNS boolean AS $$
        DECLARE
          source_identity record;
          current_state record;
          observed_now timestamptz;
        BEGIN
          SELECT raw.collector_session_id,raw.stream,normalized.symbol
          INTO source_identity
          FROM normalized_market_events normalized
          JOIN raw_market_events raw ON raw.id=normalized.raw_event_id
          WHERE normalized.id=p_market_event_id;
          IF NOT FOUND THEN RETURN false; END IF;

          -- Match the market writer's watermark -> market -> collector row order.
          -- The table lock comes afterwards and the final query rechecks latest-session
          -- identity, closing an insert race without inverting the quality-writer order.
          PERFORM 1 FROM stream_watermark_projections watermark
          WHERE watermark.collector_session_id=source_identity.collector_session_id
            AND watermark.stream=source_identity.stream FOR SHARE;
          IF NOT FOUND THEN RETURN false; END IF;
          PERFORM 1 FROM market_status_projections market
          WHERE market.symbol=source_identity.symbol FOR SHARE;
          IF NOT FOUND THEN RETURN false; END IF;
          PERFORM 1 FROM collector_sessions collector
          WHERE collector.id=source_identity.collector_session_id FOR SHARE;
          IF NOT FOUND THEN RETURN false; END IF;
          -- Fail closed instead of waiting behind a concurrent session insert: that
          -- inserter may next need the watermark/market rows already held above.
          LOCK TABLE collector_sessions IN SHARE MODE NOWAIT;
          observed_now := clock_timestamp();

          SELECT (
            normalized.source='binance_spot_public'
            AND normalized.event_type='book_ticker'
            AND normalized.quality_status='healthy'
            AND normalized.quality_reasons='[]'::jsonb
            AND raw.source=normalized.source
            AND raw.symbol=normalized.symbol
            AND raw.record_kind='stream_message'
            AND raw.payload_hash=normalized.raw_payload_hash
            AND raw.collector_session_id::text=normalized.correlation_id
            AND raw.collector_session_id::text=normalized.stream_watermark->>'session_id'
            AND raw.stream=normalized.stream_watermark->>'stream'
            AND raw.sequence=normalized.sequence
            AND raw.received_at=normalized.received_at
            AND normalized.stream_watermark->>'last_sequence'=normalized.sequence::text
            AND collector.source='binance_spot_public'
            AND collector.allowlist_version=
                'binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5'
            AND collector.status IN ('healthy','degraded')
            AND collector.ended_at IS NULL
            AND collector.started_at<=normalized.received_at
            AND collector.id=(
              SELECT latest.id FROM collector_sessions latest
              WHERE latest.source='binance_spot_public' AND latest.ended_at IS NULL
              ORDER BY latest.started_at DESC,latest.id DESC LIMIT 1)
            AND watermark.quality_status='healthy'
            AND watermark.last_sequence>=normalized.sequence
            AND watermark.observed_at>=normalized.received_at
            AND watermark.observed_at<=observed_now
            AND observed_now-watermark.observed_at<=interval '5 seconds'
            AND market.quality_status='healthy'
            AND market.quality_reasons='[]'::jsonb
            AND market.stream_watermark->>'session_id'=raw.collector_session_id::text
            AND market.stream_watermark ?& array[
              'session_id','stream','last_sequence','observed_at']
            AND (market.stream_watermark->>'observed_at')::timestamptz<=observed_now
            AND observed_now-(market.stream_watermark->>'observed_at')::timestamptz
                <=interval '5 seconds'
            AND normalized.event_time<=observed_now
            AND normalized.received_at<=observed_now
            AND observed_now-normalized.event_time<=interval '5 seconds'
            AND observed_now-normalized.received_at<=interval '5 seconds'
          ) AS is_healthy
          INTO current_state
          FROM normalized_market_events normalized
          JOIN raw_market_events raw ON raw.id=normalized.raw_event_id
          JOIN collector_sessions collector ON collector.id=raw.collector_session_id
          JOIN stream_watermark_projections watermark
            ON watermark.collector_session_id=raw.collector_session_id
           AND watermark.stream=raw.stream
          JOIN market_status_projections market ON market.symbol=normalized.symbol
          WHERE normalized.id=p_market_event_id;
          RETURN COALESCE(current_state.is_healthy,false);
        EXCEPTION WHEN OTHERS THEN
          RETURN false;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION paper_recorded_book_market_is_current_v1(varchar) FROM PUBLIC"
    )
    op.execute("""
        CREATE FUNCTION enforce_phase7_attempt_binding() RETURNS trigger AS $$
        DECLARE authorized_row record;
        BEGIN
          IF NEW.namespace='test' THEN
            RETURN NULL;
          END IF;
          SELECT * INTO authorized_row FROM paper_execution_authorizations
          WHERE authorization_id=NEW.paper_execution_authorization_id;
          IF NOT FOUND OR NEW.authorization_id<>authorized_row.authorization_id
             OR NEW.authorization_nonce<>authorized_row.authorization_nonce
             OR NEW.account_id<>authorized_row.paper_account_id
             OR NEW.created_at<authorized_row.issued_at
             OR (NEW.created_at>authorized_row.expires_at AND
                 (NEW.outcome<>'BLOCKED' OR NEW.reason_code<>'AUTHORIZATION_EXPIRED'))
             OR (EXISTS (
                   SELECT 1 FROM paper_approval_revocations revocation
                   WHERE revocation.approval_id=authorized_row.approval_id)
                 AND (NEW.outcome<>'BLOCKED' OR NEW.reason_code<>'AUTHORIZATION_REVOKED'))
          THEN
            RAISE EXCEPTION 'Paper authorization attempt is not bound to one current authorization';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_phase7_attempt_binding() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_authorization_attempt_phase7_binding
        AFTER INSERT ON paper_authorization_attempts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase7_attempt_binding()
    """)
    op.execute("""
        CREATE FUNCTION enforce_phase7_broker_input_binding() RETURNS trigger AS $$
        BEGIN
          IF NEW.source_kind<>'PAPER_AUTHORIZATION' THEN
            RETURN NULL;
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM paper_execution_authorizations authz
            JOIN paper_accounts account ON account.account_id=authz.paper_account_id
            JOIN paper_authorization_attempts attempt
              ON attempt.paper_execution_authorization_id=authz.authorization_id
             AND attempt.namespace='paper'
            JOIN paper_command_receipts receipt
              ON receipt.scope=attempt.command_scope
             AND receipt.idempotency_key=attempt.idempotency_key
             AND receipt.authorization_id=attempt.authorization_id
             AND receipt.broker_seq=NEW.broker_seq
            WHERE authz.authorization_id=NEW.paper_execution_authorization_id
              AND authz.paper_account_id=NEW.account_id
              AND authz.authorization_id=NEW.source_key
              AND authz.authorization_input_digest=NEW.payload_hash
              AND account.namespace='paper'
          ) THEN
            RAISE EXCEPTION 'Production Paper broker input is not hash-bound to its execution authorization';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_phase7_broker_input_binding() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_broker_input_phase7_binding
        AFTER INSERT ON paper_broker_inputs DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase7_broker_input_binding()
    """)

    op.execute(r"""
        DO $$
        DECLARE definition text; replaced text;
        BEGIN
          definition := pg_get_functiondef('assert_paper_relational_consistency()'::regprocedure);
          replaced := regexp_replace(
            definition,
            'input\.source_kind\s*<>\s*''TEST_COMMAND''(::text)?',
            '(attempt.namespace=''test'' AND input.source_kind<>''TEST_COMMAND'') OR (attempt.namespace=''paper'' AND input.source_kind<>''PAPER_AUTHORIZATION'')',
            'g'
          );
          replaced := replace(
            replaced,
            '''paper.order.filled.v1'',''paper.order.cancelled.v1'',''paper.order.cancelled.v2'')',
            '''paper.order.filled.v1'',''paper.order.cancelled.v1'',''paper.order.cancelled.v2'',''paper.order.accepted.v2'')'
          );
          replaced := replace(
            replaced,
            'latest.event_type<>''paper.order.accepted.v1''',
            'latest.event_type NOT IN (''paper.order.accepted.v1'',''paper.order.accepted.v2'')'
          );
          replaced := replace(
            replaced,
            'latest.event_type<>''paper.order.cancelled.v1''',
            'latest.event_type NOT IN (''paper.order.cancelled.v1'',''paper.order.cancelled.v2'')'
          );
          replaced := replace(
            replaced,
            '''paper.order.filled.v1'', ''paper.order.cancelled.v1''',
            '''paper.order.filled.v1'', ''paper.order.cancelled.v1'', ''paper.order.cancelled.v2'''
          );
          replaced := replace(
            replaced,
            '''paper.order.filled.v1'',''paper.order.cancelled.v1'')',
            '''paper.order.filled.v1'',''paper.order.cancelled.v1'',''paper.order.cancelled.v2'')'
          );
          replaced := regexp_replace(
            replaced,
            'event\.event_type\s+NOT IN\s*\(\s*''paper\.order\.accepted\.v1''(?:::text)?,\s*''paper\.order\.partially-filled\.v1''(?:::text)?,\s*''paper\.order\.filled\.v1''(?:::text)?,\s*''paper\.order\.cancelled\.v1''(?:::text)?\)',
            'event.event_type NOT IN (''paper.order.accepted.v1'',''paper.order.partially-filled.v1'',''paper.order.filled.v1'',''paper.order.cancelled.v1'',''paper.order.accepted.v2'',''paper.order.cancelled.v2'')'
          );
          replaced := regexp_replace(
            replaced,
            'event\.event_type\s+NOT IN\s*\(\s*''paper\.order\.accepted\.v1''(?:::text)?,\s*''paper\.order\.partially-filled\.v1''(?:::text)?,\s*''paper\.order\.filled\.v1''(?:::text)?,\s*''paper\.order\.cancelled\.v1''(?:::text)?,\s*''paper\.order\.cancelled\.v2''(?:::text)?\)',
            'event.event_type NOT IN (''paper.order.accepted.v1'',''paper.order.partially-filled.v1'',''paper.order.filled.v1'',''paper.order.cancelled.v1'',''paper.order.accepted.v2'',''paper.order.cancelled.v2'')'
          );
          replaced := replace(
            replaced,
            '''paper.order.filled.v1'',''paper.order.cancelled.v1'')\n               OR (event.order_version=1',
            '''paper.order.filled.v1'',''paper.order.cancelled.v1'',\n                    ''paper.order.accepted.v2'',''paper.order.cancelled.v2'')\n               OR (event.order_version=1'
          );
          replaced := replace(
            replaced,
            'event.event_type<>''paper.order.accepted.v1''\n                    OR input.source_kind IS DISTINCT FROM ''TEST_COMMAND''',
            'event.event_type NOT IN (''paper.order.accepted.v1'',''paper.order.accepted.v2'')\n                    OR (event.event_type=''paper.order.accepted.v1'' AND input.source_kind IS DISTINCT FROM ''TEST_COMMAND'')\n                    OR (event.event_type=''paper.order.accepted.v2'' AND input.source_kind IS DISTINCT FROM ''PAPER_AUTHORIZATION'')'
          );
          replaced := regexp_replace(
            replaced,
            'event\.order_version\s*=\s*1\s+AND\s*\(\s*event\.event_type\s*<>\s*''paper\.order\.accepted\.v1''\s+OR\s+input\.source_kind\s+IS DISTINCT FROM\s+''TEST_COMMAND''\s+OR\s+input\.broker_seq\s+IS DISTINCT FROM\s+paper_order\.accepted_broker_seq\s*\)',
            'event.order_version=1 AND (event.event_type NOT IN (''paper.order.accepted.v1'',''paper.order.accepted.v2'') OR (event.event_type=''paper.order.accepted.v1'' AND input.source_kind IS DISTINCT FROM ''TEST_COMMAND'') OR (event.event_type=''paper.order.accepted.v2'' AND input.source_kind IS DISTINCT FROM ''PAPER_AUTHORIZATION'') OR input.broker_seq IS DISTINCT FROM paper_order.accepted_broker_seq)'
          );
          replaced := regexp_replace(
            replaced,
            'candidate\.symbol\s*=\s*paper_order\.symbol',
            'candidate.symbol=paper_order.symbol AND candidate.side=paper_order.side',
            'g'
          );
          replaced := replace(
            replaced,
            'event.order_version>1 AND event.event_type=''paper.order.accepted.v1''',
            'event.order_version>1 AND event.event_type IN (''paper.order.accepted.v1'',''paper.order.accepted.v2'')'
          );
          replaced := replace(
            replaced,
            'event.event_type=''paper.order.cancelled.v1'' AND (',
            'event.event_type=''paper.order.cancelled.v1'' AND ('
          );
          replaced := replace(
            replaced,
            'receipt.outcome=''ORDER_CANCELLED'')))\n          ) OR EXISTS (\n            SELECT 1 FROM paper_order_events event\n            LEFT JOIN outbox_events',
            'receipt.outcome=''ORDER_CANCELLED'')))\n               OR (event.event_type=''paper.order.cancelled.v2'' AND NOT EXISTS (\n                    SELECT 1 FROM paper_cancel_command_receipts cancel_receipt\n                    WHERE cancel_receipt.order_id=event.order_id\n                      AND cancel_receipt.expected_version+1=event.order_version\n                      AND cancel_receipt.idempotency_key=event.source_key))\n          ) OR EXISTS (\n            SELECT 1 FROM paper_order_events event\n            LEFT JOIN outbox_events'
          );
          replaced := replace(
            replaced,
            'WHERE paper_order.status=''CANCELLED''\n              AND (cancelled_receipts.receipt_count<>1',
            'WHERE paper_order.status=''CANCELLED''\n              AND EXISTS (SELECT 1 FROM paper_authorization_attempts a WHERE a.authorization_id=paper_order.authorization_id AND a.namespace=''test'')\n              AND (cancelled_receipts.receipt_count<>1'
          );
          replaced := replace(
            replaced,
            'WHERE event.order_id=paper_order.order_id\n                AND event.event_type=''paper.order.cancelled.v1''',
            'WHERE event.order_id=paper_order.order_id\n                AND event.event_type IN (''paper.order.cancelled.v1'',''paper.order.cancelled.v2'')'
          );
          replaced := regexp_replace(
            replaced,
            'WHERE event\.order_id=paper_order\.order_id\s+AND event\.event_type=''paper\.order\.cancelled\.v1''',
            'WHERE event.order_id=paper_order.order_id AND event.event_type IN (''paper.order.cancelled.v1'',''paper.order.cancelled.v2'')',
            'g'
          );
          replaced := replace(
            replaced,
            'cancelled_receipts.receipt_count<>1 AND NOT EXISTS (SELECT 1 FROM paper_kill_cancel_items kill_item WHERE kill_item.order_id=paper_order.order_id)',
            'cancelled_receipts.receipt_count<>1 AND NOT EXISTS (SELECT 1 FROM paper_kill_cancel_items kill_item WHERE kill_item.order_id=paper_order.order_id) AND NOT EXISTS (SELECT 1 FROM paper_cancel_command_receipts cancel_receipt WHERE cancel_receipt.order_id=paper_order.order_id)'
          );
          IF replaced=definition THEN
            RAISE EXCEPTION 'Phase 7 could not activate the production Paper authorization ingress';
          END IF;
          EXECUTE replaced;
        END $$
    """)
    op.execute(
        "ALTER FUNCTION append_paper_outbox(varchar,varchar,jsonb,varchar,timestamptz,"
        "varchar,varchar,bigint) RENAME TO append_paper_outbox_phase4_v1"
    )
    op.execute(r"""
        CREATE FUNCTION phase7_canonical_json(p_value jsonb) RETURNS text AS $$
        DECLARE result text;
        BEGIN
          CASE jsonb_typeof(p_value)
            WHEN 'object' THEN
              SELECT '{'||COALESCE(string_agg(to_jsonb(key)::text||':'||
                     phase7_canonical_json(value),',' ORDER BY key),'')||'}'
                INTO result FROM jsonb_each(p_value);
            WHEN 'array' THEN
              SELECT '['||COALESCE(string_agg(phase7_canonical_json(value),',' ORDER BY ordinal),'')||']'
                INTO result FROM jsonb_array_elements(p_value) WITH ORDINALITY item(value,ordinal);
            ELSE result := p_value::text;
          END CASE;
          RETURN result;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE STRICT
    """)
    op.execute("REVOKE ALL ON FUNCTION phase7_canonical_json(jsonb) FROM PUBLIC")
    op.execute(r"""
        CREATE FUNCTION append_paper_outbox(
          p_event_id varchar, p_event_type varchar, p_payload jsonb,
          p_payload_hash varchar, p_occurred_at timestamptz,
          p_aggregate_type varchar, p_aggregate_id varchar, p_aggregate_version bigint
        ) RETURNS void AS $$
        DECLARE event_material text;
        BEGIN
          IF p_event_type NOT IN (
               'paper.authorization.blocked.v2','paper.authorization.consumed.v2',
               'paper.order.accepted.v2','paper.order.cancelled.v2',
               'ledger.transaction.posted.v2') THEN
            PERFORM append_paper_outbox_phase4_v1(
              p_event_id,p_event_type,p_payload,p_payload_hash,p_occurred_at,
              p_aggregate_type,p_aggregate_id,p_aggregate_version);
            RETURN;
          END IF;
          IF p_event_type NOT IN (
               'paper.authorization.blocked.v2','paper.authorization.consumed.v2',
               'paper.order.accepted.v2','paper.order.cancelled.v2',
               'ledger.transaction.posted.v2')
             OR p_event_id !~ '^[a-f0-9]{64}$' OR p_payload_hash !~ '^[a-f0-9]{64}$'
             OR p_aggregate_id !~ '^[a-f0-9]{64}$' OR p_aggregate_version<1
             OR p_payload->>'event_id' IS DISTINCT FROM p_event_id
             OR p_payload->>'event_type' IS DISTINCT FROM p_event_type
             OR p_payload->>'payload_hash' IS DISTINCT FROM p_payload_hash
             OR p_payload->>'producer' IS DISTINCT FROM 'paper-engine'
             OR p_payload->>'spec_version' IS DISTINCT FROM 'woozoo.event/v1'
             OR p_payload->'event_version' IS DISTINCT FROM '2'::jsonb
             OR p_payload->'activation_phase' IS DISTINCT FROM '7'::jsonb
             OR p_payload->>'aggregate_id' IS DISTINCT FROM p_aggregate_id
             OR (p_payload->>'aggregate_version')::bigint<>p_aggregate_version
             OR (p_payload->>'occurred_at')::timestamptz IS DISTINCT FROM p_occurred_at
             OR jsonb_typeof(p_payload->'data')<>'object'
             OR NOT (p_payload ?& ARRAY['spec_version','event_id','event_type','event_version',
                  'occurred_at','producer','activation_phase','aggregate_id','aggregate_version',
                  'payload_hash','data'])
             OR (SELECT count(*) FROM jsonb_object_keys(p_payload))<>11
             OR p_payload_hash<>encode(digest(convert_to(
                  phase7_canonical_json(p_payload->'data'),'UTF8'),'sha256'),'hex')
          THEN RAISE EXCEPTION 'Invalid closed Phase 7 Paper outbox envelope'; END IF;

          event_material := '['||to_jsonb('event'::text)::text||','||
            to_jsonb(p_event_type::text)::text||','||to_jsonb(p_aggregate_id::text)::text||','||
            to_jsonb(p_aggregate_version::text)::text||']';
          IF p_event_id<>encode(digest(convert_to(event_material,'UTF8'),'sha256'),'hex')
          THEN RAISE EXCEPTION 'Invalid Phase 7 Paper event identity'; END IF;

          IF (p_event_type LIKE 'paper.authorization.%' AND
                p_aggregate_type IS DISTINCT FROM 'paper_authorization')
             OR (p_event_type LIKE 'paper.order.%' AND
                p_aggregate_type IS DISTINCT FROM 'paper_order')
             OR (p_event_type='ledger.transaction.posted.v2' AND
                p_aggregate_type IS DISTINCT FROM 'paper_ledger')
          THEN RAISE EXCEPTION 'Invalid Phase 7 Paper aggregate type'; END IF;

          IF p_event_type='paper.authorization.blocked.v2' AND NOT EXISTS (
               SELECT 1 FROM paper_authorization_attempts attempt
               JOIN paper_execution_authorizations authz
                 ON authz.authorization_id=attempt.paper_execution_authorization_id
               WHERE attempt.authorization_id=p_aggregate_id
                 AND attempt.authorization_nonce=p_payload->'data'->>'authorization_nonce'
                 AND authz.approval_id=p_payload->'data'->>'approval_id'
                 AND attempt.request_hash=p_payload->'data'->>'request_hash'
                 AND attempt.outcome='BLOCKED'
                 AND attempt.reason_code=p_payload->'data'->>'reason_code'
                 AND p_payload->'data'->>'outcome'='BLOCKED'
                 AND (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))=6)
             OR p_event_type='paper.authorization.consumed.v2' AND NOT EXISTS (
               SELECT 1 FROM paper_authorization_attempts attempt
               JOIN paper_execution_authorizations authz
                 ON authz.authorization_id=attempt.paper_execution_authorization_id
               JOIN paper_orders orders ON orders.authorization_id=attempt.authorization_id
               WHERE attempt.authorization_id=p_aggregate_id
                 AND attempt.authorization_nonce=p_payload->'data'->>'authorization_nonce'
                 AND authz.approval_id=p_payload->'data'->>'approval_id'
                 AND attempt.request_hash=p_payload->'data'->>'request_hash'
                 AND attempt.outcome='CONSUMED_ORDER_CREATED'
                 AND p_payload->'data'->>'outcome'='CONSUMED_ORDER_CREATED'
                 AND orders.order_id=p_payload->'data'->>'order_id'
                 AND (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))=6)
             OR p_event_type='paper.order.accepted.v2' AND NOT EXISTS (
               SELECT 1 FROM paper_order_events event JOIN paper_orders orders USING(order_id)
               WHERE event.event_id=p_event_id AND event.order_id=p_aggregate_id
                 AND event.order_version=p_aggregate_version
                 AND event.event_type=p_event_type AND event.payload_hash=p_payload_hash
                 AND p_payload->'data'->>'order_id'=orders.order_id
                 AND p_payload->'data'->>'authorization_id'=orders.authorization_id)
             OR p_event_type='paper.order.cancelled.v2' AND NOT EXISTS (
               SELECT 1 FROM paper_order_events event
               JOIN paper_cancel_command_receipts receipt
                 ON receipt.order_id=event.order_id
                AND receipt.expected_version+1=event.order_version
                AND receipt.idempotency_key=event.source_key
               WHERE event.event_id=p_event_id AND event.order_id=p_aggregate_id
                 AND event.order_version=p_aggregate_version
                 AND event.event_type=p_event_type AND event.payload_hash=p_payload_hash
                 AND p_payload->'data'->>'request_hash'=receipt.request_hash
                 AND p_payload->'data'->'order'->>'order_id'=receipt.order_id
                 AND (p_payload->'data'->'order'->>'version')::bigint=receipt.expected_version+1)
             OR p_event_type='ledger.transaction.posted.v2' AND NOT EXISTS (
               SELECT 1 FROM paper_ledger_transactions tx
               JOIN paper_authorization_attempts attempt
                 ON attempt.authorization_id=p_payload->'data'->>'authorization_id'
               WHERE tx.transaction_id=p_aggregate_id
                 AND p_payload->'data'->>'transaction_id'=tx.transaction_id
                 AND (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))=2)
          THEN RAISE EXCEPTION 'Unlinked Phase 7 Paper outbox event'; END IF;

          INSERT INTO outbox_events
            (event_id,event_type,payload,payload_hash,occurred_at,
             aggregate_type,aggregate_id,aggregate_version)
          VALUES (p_event_id,p_event_type,p_payload,p_payload_hash,p_occurred_at,
                  p_aggregate_type,p_aggregate_id,p_aggregate_version);
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION append_paper_outbox(varchar,varchar,jsonb,varchar,"
        "timestamptz,varchar,varchar,bigint) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION append_paper_outbox(varchar,varchar,jsonb,varchar,"
        "timestamptz,varchar,varchar,bigint) TO woozoo_paper_engine"
    )
    op.execute(r"""
        CREATE FUNCTION enforce_paper_cancel_receipt_v1() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM paper_orders orders
            JOIN paper_order_events domain
              ON domain.order_id=orders.order_id
             AND domain.order_version=NEW.expected_version+1
             AND domain.event_type='paper.order.cancelled.v2'
             AND domain.source_key=NEW.idempotency_key
            JOIN outbox_events event
              ON event.event_id=domain.event_id
             AND event.event_type=domain.event_type
             AND event.aggregate_id=domain.order_id
             AND event.aggregate_version=domain.order_version
             AND event.payload_hash=domain.payload_hash
            JOIN paper_outbox_links link ON link.event_id=event.event_id
             AND link.account_id=orders.account_id
            WHERE orders.order_id=NEW.order_id
              AND orders.status='CANCELLED'
              AND orders.version=NEW.expected_version+1
              AND orders.held_amount=0
              AND event.payload->'data'->>'request_hash'=NEW.request_hash
              AND event.payload->'data'->'order'->>'order_id'=NEW.order_id
              AND event.payload->'data'->'order'->>'status'='CANCELLED'
              AND (event.payload->'data'->'order'->>'version')::bigint=orders.version)
          THEN RAISE EXCEPTION 'Paper cancel receipt is not atomically bound to its cancelled event';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_paper_cancel_receipt_v1() FROM PUBLIC")
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_cancel_receipt_binding
        AFTER INSERT ON paper_cancel_command_receipts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_paper_cancel_receipt_v1()
    """)
    op.execute(
        f"""
        INSERT INTO paper_accounts(account_id,namespace,created_at)
        VALUES ('{PAPER_DEFAULT_ACCOUNT_ID}','paper','2026-07-20T00:00:00Z')
    """
    )
    op.execute(
        f"""
        INSERT INTO paper_asset_balances(account_id,asset,available,held,version)
        VALUES ('{PAPER_DEFAULT_ACCOUNT_ID}','USDT',10000,0,0)
    """
    )
    op.execute(
        f"""
        INSERT INTO paper_ledger_transactions
          (transaction_id,account_id,business_event_type,business_event_id,journal_kind,posted_at)
        VALUES
          ('{PAPER_BOOTSTRAP_TRANSACTION_ID}','{PAPER_DEFAULT_ACCOUNT_ID}',
           'paper.seed','{PAPER_BOOTSTRAP_EVENT_ID}','PHYSICAL','2026-07-20T00:00:00Z')
    """
    )
    op.execute(
        f"""
        INSERT INTO paper_ledger_entries
          (transaction_id,line_no,account_code,commodity,debit,credit)
        VALUES
          ('{PAPER_BOOTSTRAP_TRANSACTION_ID}',0,'paper.available','USDT',10000,0),
          ('{PAPER_BOOTSTRAP_TRANSACTION_ID}',1,'paper.opening-equity','USDT',0,10000)
    """
    )

    op.execute(r"""
        DO $$
        DECLARE definition text; replaced text;
        BEGIN
          definition := pg_get_functiondef('assert_kill_activation_consistency()'::regprocedure);
          replaced := regexp_replace(
            definition,
            'decision\.risk_input->>''risk_input_schema_version''\s+NOT IN \(''woozoo\.risk-input/v1'',''woozoo\.risk-input/v2''\)',
            'decision.risk_input->>''risk_input_schema_version'' NOT IN (''woozoo.risk-input/v1'',''woozoo.risk-input/v2'',''woozoo.risk-input/v3'')'
          );
          replaced := replace(
            replaced,
            'decision.risk_input->>''namespace'' IS DISTINCT FROM ''test''',
            'NOT ((decision.risk_input->>''risk_input_schema_version'' IN (''woozoo.risk-input/v1'',''woozoo.risk-input/v2'') AND decision.risk_input->>''namespace''=''test'') OR (decision.risk_input->>''risk_input_schema_version''=''woozoo.risk-input/v3'' AND decision.risk_input->>''namespace''=''paper''))'
          );
          replaced := replace(
            replaced,
            'outbox.event_type IS DISTINCT FROM ''risk.decision.recorded.v1''',
            'outbox.event_type IS DISTINCT FROM CASE WHEN decision.risk_input->>''risk_input_schema_version''=''woozoo.risk-input/v3'' THEN ''risk.decision.recorded.v2'' ELSE ''risk.decision.recorded.v1'' END'
          );
          replaced := replace(
            replaced,
            '''event'',''risk.decision.recorded.v1'',decision.decision_id,''1''',
            '''event'',CASE WHEN decision.risk_input->>''risk_input_schema_version''=''woozoo.risk-input/v3'' THEN ''risk.decision.recorded.v2'' ELSE ''risk.decision.recorded.v1'' END,decision.decision_id,''1'''
          );
          replaced := replace(
            replaced,
            'outbox.payload->''event_version'' IS DISTINCT FROM ''1''::jsonb
                   OR (outbox.payload->>''occurred_at'')::timestamptz',
            'outbox.payload->''event_version'' IS DISTINCT FROM CASE WHEN decision.risk_input->>''risk_input_schema_version''=''woozoo.risk-input/v3'' THEN ''2''::jsonb ELSE ''1''::jsonb END
                   OR (outbox.payload->>''occurred_at'')::timestamptz'
          );
          replaced := regexp_replace(
            replaced,
            'LEFT JOIN kill_switch_state state\s+ON state\.scope=event\.scope AND state\.active\s+AND state\.version=event\.new_version\s+AND state\.last_activation_event_id=event\.activation_event_id\s+WHERE state\.scope IS NULL',
            'LEFT JOIN kill_switch_state state ON state.scope=event.scope WHERE state.scope IS NULL OR state.version<event.new_version OR (state.version=event.new_version AND (NOT state.active OR state.last_activation_event_id<>event.activation_event_id))'
          );
          IF replaced=definition THEN
            RAISE EXCEPTION 'Phase 7 could not widen the Risk/Kill consistency oracle';
          END IF;
          EXECUTE replaced;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_phase6_risk_proposal() RETURNS trigger AS $$
        BEGIN
          IF NEW.risk_input->>'risk_input_schema_version'='woozoo.risk-input/v1' THEN
            IF NEW.proposal_id IS NOT NULL OR NEW.risk_input->>'namespace'<>'test' THEN
              RAISE EXCEPTION 'Risk v1 binding is invalid';
            END IF;
          ELSIF NEW.risk_input->>'risk_input_schema_version' IN
                ('woozoo.risk-input/v2','woozoo.risk-input/v3') THEN
            IF (NEW.risk_input->>'risk_input_schema_version'='woozoo.risk-input/v2'
                  AND NEW.risk_input->>'namespace'<>'test')
               OR (NEW.risk_input->>'risk_input_schema_version'='woozoo.risk-input/v3'
                  AND NEW.risk_input->>'namespace'<>'paper')
               OR NEW.proposal_id IS NULL OR NOT EXISTS (
                  SELECT 1 FROM trade_proposals proposal
                  WHERE proposal.proposal_id=NEW.proposal_id
                    AND proposal.proposal_hash=NEW.proposal_hash
                    AND proposal.payload=NEW.risk_input->'proposal'->'payload'
                    AND proposal.risk_eligible=true
                    AND proposal.side IN ('BUY','SELL'))
            THEN
              RAISE EXCEPTION 'Risk Proposal binding is incomplete';
            END IF;
          ELSE
            RAISE EXCEPTION 'Risk input version is not approved';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute(r"""
        CREATE FUNCTION persist_risk_decision_v1(decision_record jsonb, event_record jsonb)
        RETURNS TABLE(created boolean, outbox_event_id varchar) AS $$
        DECLARE
          prior jsonb;
          prior_event varchar;
        BEGIN
          IF jsonb_typeof(decision_record)<>'object'
             OR jsonb_typeof(event_record)<>'object'
             OR decision_record->>'decision_id' IS NULL
             OR decision_record->>'risk_input_digest' IS NULL
             OR event_record->>'event_id' IS NULL
          THEN
            RAISE EXCEPTION 'Risk decision command is invalid';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'risk-input:'||(decision_record->>'risk_input_digest'),0));
          SELECT jsonb_build_object(
            'decision_id',decision_id,'risk_input_digest',risk_input_digest,
            'risk_input',risk_input,'decision_hash',decision_hash,'verdict',verdict,
            'primary_reason',primary_reason,'ordered_reason_codes',ordered_reason_codes,
            'policy_version',policy_version,'proposal_hash',proposal_hash,
            'portfolio_snapshot_hash',portfolio_snapshot_hash,
            'data_state_hash',data_state_hash,
            'paper_order_preview_hash',paper_order_preview_hash,
            'reconciliation_checkpoint_hash',reconciliation_checkpoint_hash,
            'kill_switch_version',kill_switch_version,
            'decision_as_of',to_jsonb(decision_as_of),
            'recorded_at',to_jsonb(recorded_at),'proposal_id',proposal_id)
            INTO prior FROM risk_decisions
            WHERE risk_input_digest=decision_record->>'risk_input_digest';
          IF prior IS NOT NULL THEN
            IF prior<>decision_record THEN
              RAISE EXCEPTION 'RISK_INPUT_IDEMPOTENCY_CONFLICT';
            END IF;
            SELECT event_id INTO prior_event FROM risk_outbox_links
            WHERE aggregate_kind='risk-decision'
              AND aggregate_id=decision_record->>'decision_id';
            IF prior_event IS NULL OR prior_event<>event_record->>'event_id' THEN
              RAISE EXCEPTION 'RISK_DECISION_OUTBOX_MISSING';
            END IF;
            RETURN QUERY SELECT false,prior_event;
            RETURN;
          END IF;
          INSERT INTO risk_decisions(
            decision_id,risk_input_digest,risk_input,decision_hash,verdict,primary_reason,
            ordered_reason_codes,policy_version,proposal_hash,portfolio_snapshot_hash,
            data_state_hash,paper_order_preview_hash,reconciliation_checkpoint_hash,
            kill_switch_version,decision_as_of,recorded_at,proposal_id)
          VALUES (
            decision_record->>'decision_id',decision_record->>'risk_input_digest',
            decision_record->'risk_input',decision_record->>'decision_hash',
            decision_record->>'verdict',decision_record->>'primary_reason',
            decision_record->'ordered_reason_codes',decision_record->>'policy_version',
            decision_record->>'proposal_hash',decision_record->>'portfolio_snapshot_hash',
            decision_record->>'data_state_hash',decision_record->>'paper_order_preview_hash',
            decision_record->>'reconciliation_checkpoint_hash',
            (decision_record->>'kill_switch_version')::bigint,
            (decision_record->>'decision_as_of')::timestamptz,
            (decision_record->>'recorded_at')::timestamptz,
            decision_record->>'proposal_id');
          INSERT INTO outbox_events(
            event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,
            aggregate_id,aggregate_version)
          VALUES (
            event_record->>'event_id',event_record->>'event_type',event_record,
            event_record->>'payload_hash',(event_record->>'occurred_at')::timestamptz,
            'risk_decision',decision_record->>'decision_id',1);
          INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id)
          VALUES (event_record->>'event_id','risk-decision',decision_record->>'decision_id');
          RETURN QUERY SELECT true,(event_record->>'event_id')::varchar;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION persist_risk_decision_v1(jsonb,jsonb) FROM PUBLIC")
    op.execute(r"""
        CREATE FUNCTION load_authoritative_risk_context_v1(
          p_proposal_id varchar,p_paper_account_id varchar,p_recorded_at timestamptz
        ) RETURNS jsonb AS $$
        DECLARE result jsonb; existing_input jsonb;
        BEGIN
          IF p_proposal_id !~ '^[a-f0-9]{64}$'
             OR p_paper_account_id !~ '^[a-f0-9]{64}$'
          THEN RAISE EXCEPTION 'RISK_CONTEXT_ID_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'paper-account:'||p_paper_account_id,0));
          IF NOT EXISTS (SELECT 1 FROM paper_accounts account
                         WHERE account.account_id=p_paper_account_id
                           AND account.namespace='paper')
          THEN RAISE EXCEPTION 'RISK_PAPER_ACCOUNT_NOT_FOUND'; END IF;
          SELECT decision.risk_input INTO existing_input
          FROM risk_decisions decision WHERE decision.proposal_id=p_proposal_id
          ORDER BY decision.recorded_at,decision.decision_id LIMIT 1;
          IF FOUND THEN
            RETURN jsonb_build_object('existing_risk_input',existing_input);
          END IF;
          SELECT jsonb_build_object(
            'proposal',proposal.payload,
            'proposal_hash',proposal.proposal_hash,
            'evidence',jsonb_build_object(
              'evidence_id',snapshot.evidence_id,
              'evidence_digest',snapshot.evidence_digest,
              'as_of',snapshot.as_of,
              'knowledge_cutoff',snapshot.knowledge_cutoff,
              'quality_status',snapshot.quality_status),
            'balances',(SELECT jsonb_build_object(
                'BTC',jsonb_build_object('available','0','held','0','version',0),
                'ETH',jsonb_build_object('available','0','held','0','version',0))
              || COALESCE(jsonb_object_agg(balance.asset,
                   jsonb_build_object('available',balance.available::text,
                     'held',balance.held::text,'version',balance.version)),'{}'::jsonb)
              FROM paper_asset_balances balance
              WHERE balance.account_id=p_paper_account_id),
            'books',(SELECT COALESCE(jsonb_object_agg(book.symbol,
              jsonb_build_object('best_bid',book.payload->>'bid_price',
                'best_ask',book.payload->>'ask_price','event_time',book.event_time,
                'received_at',book.received_at,'quality_status',book.quality_status)),
              '{}'::jsonb) FROM (
                SELECT DISTINCT ON (event.symbol) event.symbol,event.payload,
                  event.event_time,event.received_at,event.quality_status
                FROM normalized_market_events event
                WHERE event.event_type='book_ticker' AND event.symbol IN ('BTCUSDT','ETHUSDT')
                  AND event.event_time<=p_recorded_at AND event.received_at<=p_recorded_at
                ORDER BY event.symbol,event.event_time DESC,event.received_at DESC,event.id DESC
              ) book),
            'open_orders',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'client_order_id',orders.client_order_id,'symbol',orders.symbol,
                'side',orders.side,
                'remaining_quantity',
                  ((orders.quantity-orders.filled_quantity)::numeric(38,18))::text,
                'limit_price',(orders.limit_price::numeric(38,18))::text,
                'remaining_worst_case_quote_fee',CASE WHEN orders.side='BUY' THEN
                  (((orders.quantity-orders.filled_quantity)*orders.limit_price*0.001)
                    ::numeric(38,18))::text
                  ELSE '0' END,'status',orders.status,
                'accepted_broker_seq',orders.accepted_broker_seq)
              ORDER BY orders.accepted_broker_seq,orders.client_order_id,orders.order_id),
              '[]'::jsonb) FROM paper_orders orders
              WHERE orders.account_id=p_paper_account_id
                AND orders.status IN ('OPEN','PARTIALLY_FILLED')),
            'fifo_lots',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'lot_id',lot.lot_id,'symbol',lot.asset||'USDT',
                'remaining_quantity',
                  ((lot.acquired_quantity-COALESCE(used.quantity,0))::numeric(38,18))::text,
                'quote_basis',
                  ((lot.quote_cost-COALESCE(used.quote_basis,0))::numeric(38,18))::text,
                'source_fill_id',lot.source_fill_id)
              ORDER BY lot.acquired_at,lot.lot_id),'[]'::jsonb)
              FROM paper_inventory_lots lot
              LEFT JOIN LATERAL (SELECT sum(item.quantity) quantity,
                sum(item.quote_basis) quote_basis FROM paper_lot_consumptions item
                WHERE item.lot_id=lot.lot_id) used ON true
              WHERE lot.account_id=p_paper_account_id
                AND lot.acquired_quantity-COALESCE(used.quantity,0)>0),
            'realized_pnl_24h',(SELECT (COALESCE(sum(
                CASE WHEN entry.account_code='paper.realized-pnl' THEN entry.credit-entry.debit
                     WHEN entry.account_code='paper.realized-loss' THEN entry.credit-entry.debit
                     ELSE 0 END),0)::numeric(38,18))::text
              FROM paper_ledger_transactions tx JOIN paper_ledger_entries entry USING(transaction_id)
              WHERE tx.account_id=p_paper_account_id
                AND tx.posted_at>p_recorded_at-interval '24 hours'
                AND entry.commodity='USDT_VAL'),
            'prior_high_water',(SELECT (COALESCE(max(
                (decision.risk_input->'portfolio'->>'high_water_equity')::numeric),10000)
                  ::numeric(38,18))::text
              FROM risk_decisions decision
              WHERE decision.risk_input->>'namespace'='paper'),
            'kill_switch',(SELECT jsonb_build_object('active',state.active,
                'version',state.version,'event_id',state.last_activation_event_id)
              FROM kill_switch_state state WHERE state.scope='paper-global'),
            'reconciliation',(SELECT jsonb_build_object(
                'checkpoint_id',checkpoint.checkpoint_id,
                'checkpoint_hash',encode(digest(convert_to(risk_canonical_jsonb(
                  jsonb_build_object('checkpoint_id',checkpoint.checkpoint_id,
                    'health',checkpoint.status,'mismatch_codes',checkpoint.mismatch_codes)),
                  'UTF8'),'sha256'),'hex'),
                'health',checkpoint.status,'mismatch_codes',checkpoint.mismatch_codes)
              FROM paper_reconciliation_checkpoints checkpoint
              WHERE checkpoint.account_id=p_paper_account_id
              ORDER BY checkpoint.created_at DESC,checkpoint.checkpoint_id DESC LIMIT 1),
            'order_intent_seen',EXISTS(SELECT 1 FROM paper_orders orders
              WHERE orders.account_id=p_paper_account_id
                AND orders.symbol=proposal.payload->>'symbol'
                AND orders.side=proposal.payload->>'side'
                AND orders.accepted_broker_seq IN (SELECT input.broker_seq
                  FROM paper_broker_inputs input
                  WHERE input.observed_at>=p_recorded_at-interval '15 minutes')),
            'recorded_at',p_recorded_at)
          INTO result
          FROM trade_proposals proposal
          JOIN evidence_snapshots snapshot ON snapshot.evidence_id=proposal.evidence_id
          JOIN analysis_runs run ON run.run_id=proposal.run_id AND run.namespace='paper'
          WHERE proposal.proposal_id=p_proposal_id
            AND proposal.risk_eligible AND proposal.side IN ('BUY','SELL')
            AND proposal.payload->>'evidence_id'=snapshot.evidence_id
            AND proposal.payload->>'evidence_digest'=snapshot.evidence_digest
            AND (proposal.payload->>'as_of')::timestamptz=snapshot.as_of
            AND (proposal.payload->>'knowledge_cutoff')::timestamptz=snapshot.knowledge_cutoff;
          IF result IS NULL THEN RAISE EXCEPTION 'RISK_PROPOSAL_NOT_FOUND_OR_INELIGIBLE'; END IF;
          RETURN result;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION load_authoritative_risk_context_v1(varchar,varchar,timestamptz) "
        "FROM PUBLIC"
    )

    # Alembic may apply P1-P7 in one transaction; drain P5/P6 deferred checks
    # before changing the Kill state table definition.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("DROP TRIGGER kill_switch_state_activation_only ON kill_switch_state")
    op.execute("ALTER TABLE kill_switch_state DROP CONSTRAINT ck_kill_state_monotonic")
    op.add_column(
        "kill_switch_state",
        sa.Column("last_recovery_event_id", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_kill_state_phase7_recovery",
        "kill_switch_state",
        "kill_recovery_events",
        ["last_recovery_event_id"],
        ["recovery_event_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_kill_state_phase7",
        "kill_switch_state",
        "(active=false AND version=0 AND last_activation_event_id IS NULL "
        "AND last_recovery_event_id IS NULL) OR "
        "(active=true AND version>0 AND last_activation_event_id IS NOT NULL "
        "AND last_recovery_event_id IS NULL) OR "
        "(active=false AND version>0 AND last_activation_event_id IS NOT NULL "
        "AND last_recovery_event_id IS NOT NULL)",
    )
    op.execute("""
        CREATE FUNCTION enforce_kill_state_phase7_transition() RETURNS trigger AS $$
        BEGIN
          IF OLD.scope<>'paper-global' OR NEW.scope<>OLD.scope
             OR NEW.version<>OLD.version+1 OR NEW.active=OLD.active
          THEN
            RAISE EXCEPTION 'Kill state permits monotonic activation only unless an audited manual recovery is complete';
          END IF;
          IF NEW.active THEN
            IF NEW.last_recovery_event_id IS NOT NULL OR NEW.last_activation_event_id IS NULL
               OR NOT EXISTS (
                 SELECT 1 FROM kill_switch_events event
                 WHERE event.activation_event_id=NEW.last_activation_event_id
                   AND event.scope=NEW.scope AND event.prior_version=OLD.version
                   AND event.new_version=NEW.version)
            THEN RAISE EXCEPTION 'Kill activation transition is incomplete'; END IF;
          ELSE
            IF NOT OLD.active OR NEW.last_activation_event_id<>OLD.last_activation_event_id
               OR NEW.last_recovery_event_id IS NULL OR NOT EXISTS (
                 SELECT 1 FROM kill_recovery_events event
                 WHERE event.recovery_event_id=NEW.last_recovery_event_id
                   AND event.scope=NEW.scope AND event.actor_id='operator-local-1'
                   AND event.prior_version=OLD.version AND event.new_version=NEW.version)
            THEN RAISE EXCEPTION 'automatic Kill recovery is forbidden'; END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_kill_state_phase7_transition() FROM PUBLIC")
    op.execute("""
        CREATE TRIGGER kill_switch_state_phase7_transition
        BEFORE UPDATE ON kill_switch_state FOR EACH ROW
        EXECUTE FUNCTION enforce_kill_state_phase7_transition()
    """)
    op.execute("""
        CREATE FUNCTION assert_kill_recovery_consistency() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM kill_recovery_events event
            LEFT JOIN risk_kill_recovery_command_receipts receipt
              ON receipt.recovery_event_id=event.recovery_event_id
             AND receipt.idempotency_key=event.idempotency_key
             AND receipt.request_hash=event.request_hash
            LEFT JOIN kill_switch_state state ON state.scope=event.scope
            WHERE receipt.recovery_event_id IS NULL OR state.scope IS NULL
               OR state.version<event.new_version
               OR (state.version=event.new_version AND
                   (state.active OR state.last_recovery_event_id<>event.recovery_event_id))
          ) OR EXISTS (
            SELECT 1 FROM risk_kill_recovery_command_receipts receipt
            LEFT JOIN kill_recovery_events event
              ON event.recovery_event_id=receipt.recovery_event_id
            WHERE event.recovery_event_id IS NULL
          ) OR EXISTS (
            SELECT 1 FROM kill_switch_state state
            LEFT JOIN kill_recovery_events event
              ON event.recovery_event_id=state.last_recovery_event_id
             AND event.scope=state.scope AND event.new_version=state.version
            WHERE NOT state.active AND state.version>0 AND event.recovery_event_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Kill recovery transaction is incomplete';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION assert_kill_recovery_consistency() FROM PUBLIC")
    for table in (
        "kill_recovery_events",
        "risk_kill_recovery_command_receipts",
        "kill_switch_state",
    ):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {table}_phase7_recovery_consistency
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION assert_kill_recovery_consistency()
        """)

    op.execute(f"""
        CREATE FUNCTION recover_kill_switch_v1(
          p_idempotency_key varchar,p_request_hash varchar,p_expected_version bigint,
          p_activation_event_id varchar,p_incident_reference varchar,p_reason varchar,
          p_actor_id varchar,p_session_digest varchar,p_csrf_digest varchar,
          p_origin_hash varchar,p_observed_at timestamptz)
        RETURNS TABLE(created boolean,response jsonb) AS $$
        DECLARE
          prior record; state_row record; completion_row record; checkpoint_row record;
          data_material jsonb;
          data_state_hash varchar; reconciliation_hash varchar; context_digest varchar;
          recovery_event_id varchar; new_version bigint; event_data jsonb;
          event_payload jsonb; outbox_event_id varchar; event_payload_hash varchar;
        BEGIN
          IF length(p_idempotency_key) NOT BETWEEN 1 AND 128
             OR p_request_hash !~ '^[a-f0-9]{{64}}$'
             OR length(p_incident_reference) NOT BETWEEN 1 AND 128
             OR length(p_reason) NOT BETWEEN 1 AND 512
          THEN RAISE EXCEPTION 'KILL_RECOVERY_COMMAND_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'kill-recovery:'||p_idempotency_key,0));
          SELECT receipt.request_hash,receipt.response INTO prior
            FROM risk_kill_recovery_command_receipts AS receipt
            WHERE receipt.idempotency_key=p_idempotency_key;
          IF FOUND THEN
            IF prior.request_hash<>p_request_hash THEN
              RAISE EXCEPTION 'KILL_RECOVERY_IDEMPOTENCY_CONFLICT';
            END IF;
            RETURN QUERY SELECT false,prior.response;
            RETURN;
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'paper-account:{PAPER_DEFAULT_ACCOUNT_ID}',0));
          SELECT * INTO state_row FROM kill_switch_state
            WHERE scope='paper-global' FOR UPDATE;
          IF NOT FOUND OR NOT state_row.active OR state_row.version<>p_expected_version
             OR state_row.last_activation_event_id<>p_activation_event_id
          THEN RAISE EXCEPTION 'KILL_RECOVERY_VERSION_MISMATCH'; END IF;
          SELECT * INTO completion_row FROM paper_kill_cancel_completions
            WHERE activation_event_id=p_activation_event_id FOR SHARE;
          IF NOT FOUND OR EXISTS (
               SELECT 1 FROM paper_orders WHERE status IN ('OPEN','PARTIALLY_FILLED'))
          THEN RAISE EXCEPTION 'KILL_RECOVERY_CANCELLATION_INCOMPLETE'; END IF;
          IF NOT EXISTS (
            SELECT 1 FROM paper_authorization_worker_state worker
            WHERE worker.worker_name='phase7-paper-authorization'
              AND worker.status='RUNNING'
              AND worker.heartbeat_at<=CURRENT_TIMESTAMP
              AND worker.heartbeat_at>=CURRENT_TIMESTAMP-interval '2 minutes'
          ) THEN RAISE EXCEPTION 'KILL_RECOVERY_WORKER_NOT_READY'; END IF;
          SELECT jsonb_agg(jsonb_build_object(
              'symbol',latest.symbol,'event_id',latest.id,
              'best_bid',latest.payload->>'bid_price',
              'best_ask',latest.payload->>'ask_price',
              'received_at',latest.received_at) ORDER BY latest.symbol)
            INTO data_material
          FROM (
            SELECT DISTINCT ON (symbol) id,symbol,payload,quality_status,received_at
            FROM normalized_market_events
            WHERE symbol IN ('BTCUSDT','ETHUSDT') AND event_type='book_ticker'
            ORDER BY symbol,received_at DESC,sequence DESC,id DESC
          ) latest
          WHERE latest.quality_status='healthy'
            AND latest.received_at<=CURRENT_TIMESTAMP
            AND latest.received_at>=CURRENT_TIMESTAMP-interval '5 seconds'
            AND latest.payload ?& array['bid_price','ask_price'];
          IF data_material IS NULL OR jsonb_array_length(data_material)<>2 THEN
            RAISE EXCEPTION 'KILL_RECOVERY_DATA_UNHEALTHY';
          END IF;
          data_state_hash:=encode(digest(convert_to(risk_canonical_jsonb(data_material),
            'UTF8'),'sha256'),'hex');
          SELECT * INTO checkpoint_row FROM paper_reconciliation_checkpoints
            WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
            ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1;
          IF NOT FOUND OR checkpoint_row.status<>'HEALTHY'
             OR checkpoint_row.mismatch_codes<>'[]'::jsonb
             OR checkpoint_row.created_at<=completion_row.completed_at
             OR checkpoint_row.input_digest<>completion_row.state_digest
             OR EXISTS (
               SELECT 1 FROM paper_ledger_transactions tx
               JOIN paper_ledger_entries entry USING(transaction_id)
               WHERE tx.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
               GROUP BY entry.transaction_id,entry.commodity
               HAVING sum(entry.debit)<>sum(entry.credit))
             OR EXISTS (
               SELECT 1 FROM paper_ledger_transactions tx
               WHERE tx.account_id='{PAPER_DEFAULT_ACCOUNT_ID}' AND NOT EXISTS (
                                 SELECT 1 FROM paper_ledger_entries entry
                                 WHERE entry.transaction_id=tx.transaction_id))
          THEN RAISE EXCEPTION 'KILL_RECOVERY_RECONCILIATION_UNHEALTHY'; END IF;
          reconciliation_hash:=encode(digest(convert_to(risk_canonical_jsonb(
            jsonb_build_object('checkpoint_id',checkpoint_row.checkpoint_id,
              'health',checkpoint_row.status,
              'mismatch_codes',checkpoint_row.mismatch_codes)),
            'UTF8'),'sha256'),'hex');
          context_digest:=encode(digest(convert_to(risk_canonical_jsonb(
            jsonb_build_object('activation_event_id',p_activation_event_id,
              'data_state_hash',data_state_hash,
              'ledger_snapshot_hash',checkpoint_row.input_digest,
              'reconciliation_checkpoint_hash',reconciliation_hash)),
            'UTF8'),'sha256'),'hex');
          new_version:=state_row.version+1;
          recovery_event_id:=encode(digest(convert_to(risk_canonical_jsonb(
            jsonb_build_array('kill-recovery',p_idempotency_key,p_request_hash,
              p_activation_event_id,new_version)), 'UTF8'),'sha256'),'hex');
          response:=jsonb_build_object('result','RECOVERED',
            'recovery_event_id',recovery_event_id,'prior_version',state_row.version,
            'version',new_version,'activation_event_id',p_activation_event_id,
            'data_state_hash',data_state_hash,
            'reconciliation_checkpoint_hash',reconciliation_hash,
            'ledger_snapshot_hash',checkpoint_row.input_digest);
          INSERT INTO risk_kill_recovery_command_receipts(
            idempotency_key,request_hash,recovery_event_id,response,created_at)
          VALUES (p_idempotency_key,p_request_hash,recovery_event_id,response,p_observed_at);
          INSERT INTO kill_recovery_events(
            recovery_event_id,scope,idempotency_key,request_hash,actor_id,session_digest,
            csrf_token_digest,origin_hash,incident_reference,reason,observed_at,
            context_digest,data_status,data_state_hash,reconciliation_status,
            reconciliation_checkpoint_hash,ledger_status,ledger_snapshot_hash,
            prior_version,new_version)
          VALUES (recovery_event_id,'paper-global',p_idempotency_key,p_request_hash,p_actor_id,
            p_session_digest,p_csrf_digest,p_origin_hash,p_incident_reference,p_reason,
            p_observed_at,context_digest,'HEALTHY',data_state_hash,'PASS',
            reconciliation_hash,'BALANCED',checkpoint_row.input_digest,
            state_row.version,new_version);
          event_data:=jsonb_build_object(
            'recovery_event_id',recovery_event_id,'scope','paper-global','active',false,
            'prior_version',state_row.version,'version',new_version,'actor_id',p_actor_id,
            'session_binding_hash',p_session_digest,'csrf_binding_hash',p_csrf_digest,
            'origin_hash',p_origin_hash,'incident_reference',p_incident_reference,
            'reason',p_reason,'observed_at',to_jsonb(p_observed_at),
            'context_digest',context_digest,'data_status','HEALTHY',
            'data_state_hash',data_state_hash,'reconciliation_status','PASS',
            'reconciliation_checkpoint_hash',reconciliation_hash,
            'ledger_status','BALANCED','ledger_snapshot_hash',checkpoint_row.input_digest);
          event_payload_hash:=encode(digest(convert_to(risk_canonical_jsonb(event_data),
            'UTF8'),'sha256'),'hex');
          outbox_event_id:=encode(digest(convert_to(risk_canonical_jsonb(jsonb_build_array(
            'event','kill-switch.recovered.v2',recovery_event_id,'1')),
            'UTF8'),'sha256'),'hex');
          event_payload:=jsonb_build_object(
            'spec_version','woozoo.event/v1','event_id',outbox_event_id,
            'event_type','kill-switch.recovered.v2','event_version',2,
            'occurred_at',to_jsonb(p_observed_at),'producer','risk-engine',
            'activation_phase',7,'aggregate_id',recovery_event_id,'aggregate_version',1,
            'payload_hash',event_payload_hash,'data',event_data);
          INSERT INTO outbox_events(event_id,event_type,payload,payload_hash,occurred_at,
            aggregate_type,aggregate_id,aggregate_version)
          VALUES (outbox_event_id,'kill-switch.recovered.v2',event_payload,
            event_payload_hash,p_observed_at,'kill_recovery',recovery_event_id,1);
          INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id)
          VALUES (outbox_event_id,'kill-recovery',recovery_event_id);
          UPDATE kill_switch_state SET active=false,version=new_version,
            last_recovery_event_id=recovery_event_id
            WHERE scope='paper-global' AND active AND version=state_row.version
              AND last_activation_event_id=p_activation_event_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'KILL_RECOVERY_STATE_CONFLICT'; END IF;
          RETURN QUERY SELECT true,response;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION recover_kill_switch_v1(varchar,varchar,bigint,varchar,"
        "varchar,varchar,varchar,varchar,varchar,varchar,timestamptz) FROM PUBLIC"
    )
    op.execute(r"""
        CREATE FUNCTION assert_phase7_risk_outbox_consistency() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM paper_approvals domain
            LEFT JOIN risk_outbox_links link
              ON link.aggregate_kind='paper-approval' AND link.aggregate_id=domain.approval_id
            LEFT JOIN outbox_events event ON event.event_id=link.event_id
            WHERE event.event_id IS NULL OR event.event_type<>'paper.approval.recorded.v1'
              OR event.aggregate_id<>domain.approval_id
              OR event.payload->'data'->>'payload_hash'<>domain.payload_hash
          ) OR EXISTS (
            SELECT 1 FROM paper_approval_revocations domain
            LEFT JOIN risk_outbox_links link ON link.aggregate_kind='paper-approval-revocation'
              AND link.aggregate_id=domain.revocation_id
            LEFT JOIN outbox_events event ON event.event_id=link.event_id
            WHERE event.event_id IS NULL OR event.event_type<>'paper.approval.revoked.v1'
              OR event.aggregate_id<>domain.revocation_id
              OR event.payload->'data'->>'payload_hash'<>domain.payload_hash
          ) OR EXISTS (
            SELECT 1 FROM paper_execution_authorizations domain
            LEFT JOIN risk_outbox_links link ON link.aggregate_kind='paper-authorization'
              AND link.aggregate_id=domain.authorization_id
            LEFT JOIN outbox_events event ON event.event_id=link.event_id
            WHERE event.event_id IS NULL OR event.event_type<>'paper.authorization.issued.v1'
              OR event.aggregate_id<>domain.authorization_id
              OR event.payload->'data'->>'authorization_input_digest'<>
                   domain.authorization_input_digest
          ) OR EXISTS (
            SELECT 1 FROM kill_recovery_events domain
            LEFT JOIN risk_outbox_links link ON link.aggregate_kind='kill-recovery'
              AND link.aggregate_id=domain.recovery_event_id
            LEFT JOIN outbox_events event ON event.event_id=link.event_id
            WHERE event.event_id IS NULL OR event.event_type<>'kill-switch.recovered.v2'
              OR event.aggregate_id<>domain.recovery_event_id
              OR event.payload->'data'->>'context_digest'<>domain.context_digest
          ) OR EXISTS (
            SELECT 1 FROM risk_outbox_links link
            LEFT JOIN outbox_events event ON event.event_id=link.event_id
            WHERE link.aggregate_kind IN ('paper-approval','paper-approval-revocation',
                  'paper-authorization','kill-recovery')
              AND (event.event_id IS NULL OR event.payload->>'producer'<>'risk-engine'
                   OR event.payload->'event_version'<>'2'::jsonb
                   OR event.payload->>'aggregate_id'<>link.aggregate_id
                   OR event.payload_hash<>encode(digest(convert_to(
                        risk_canonical_jsonb(event.payload->'data'),'UTF8'),'sha256'),'hex')))
          THEN RAISE EXCEPTION 'Phase 7 Risk domain/outbox transaction is incomplete'; END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION assert_phase7_risk_outbox_consistency() FROM PUBLIC")
    for table in (
        "paper_approvals",
        "paper_approval_revocations",
        "paper_execution_authorizations",
        "kill_recovery_events",
        "risk_outbox_links",
        "outbox_events",
    ):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {table}_phase7_risk_outbox_consistency
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION assert_phase7_risk_outbox_consistency()
        """)

    op.execute("""
        CREATE VIEW paper_approval_view_v1 AS
        SELECT proposal.proposal_id, proposal.proposal_hash,
          CASE
            WHEN decision.decision_id IS NULL THEN 'PENDING_RISK'
            WHEN decision.verdict<>'ALLOWED' THEN 'BLOCKED'
            WHEN approval.approval_id IS NULL THEN 'READY'
            WHEN approval.decision='REJECTED' OR revocation.revocation_id IS NOT NULL
                 OR approval.expires_at<=CURRENT_TIMESTAMP THEN 'BLOCKED'
            WHEN authz.authorization_id IS NULL THEN 'APPROVED'
            WHEN attempt.outcome='BLOCKED' THEN 'BLOCKED'
            ELSE 'AUTHORIZATION_ISSUED'
          END AS status,
          decision.decision_id AS risk_decision_id,
          decision.decision_hash AS risk_decision_hash,
          decision.risk_input_digest, decision.policy_version AS risk_policy_version,
          decision.verdict AS risk_verdict,
          decision.risk_input->'order_preview' AS paper_order_preview,
          decision.paper_order_preview_hash,
          approval.approval_id, approval.decision AS approval_decision,
          approval.decided_at, approval.expires_at AS approval_expires_at,
          revocation.revocation_id, authz.authorization_id,
          attempt.outcome AS authorization_attempt_outcome
        FROM trade_proposals proposal
        LEFT JOIN LATERAL (
          SELECT * FROM risk_decisions candidate
          WHERE candidate.proposal_id=proposal.proposal_id
          ORDER BY candidate.recorded_at DESC, candidate.decision_id DESC LIMIT 1
        ) decision ON true
        LEFT JOIN paper_approvals approval ON approval.risk_decision_id=decision.decision_id
        LEFT JOIN paper_approval_revocations revocation
          ON revocation.approval_id=approval.approval_id
        LEFT JOIN paper_execution_authorizations authz
          ON authz.approval_id=approval.approval_id
        LEFT JOIN paper_authorization_attempts attempt
          ON attempt.paper_execution_authorization_id=authz.authorization_id
    """)
    op.execute("""
        CREATE VIEW paper_authorization_view_v1 AS
        SELECT authz.authorization_id, authz.approval_id,
          authz.proposal_id, authz.risk_decision_id,
          authz.paper_account_id, authz.issued_at, authz.expires_at,
          CASE
            WHEN attempt.outcome='BLOCKED' THEN 'BLOCKED'
            WHEN attempt.authorization_id IS NOT NULL THEN 'CONSUMED'
            WHEN revocation.revocation_id IS NOT NULL THEN 'REVOKED'
            WHEN authz.expires_at<=CURRENT_TIMESTAMP THEN 'EXPIRED'
            ELSE 'ISSUED'
          END AS status,
          attempt.reason_code
        FROM paper_execution_authorizations authz
        LEFT JOIN paper_approval_revocations revocation
          ON revocation.approval_id=authz.approval_id
        LEFT JOIN paper_authorization_attempts attempt
          ON attempt.paper_execution_authorization_id=authz.authorization_id
    """)
    op.execute("""
        CREATE VIEW paper_pending_authorizations_v1 AS
        SELECT authz.authorization_id,authz.issued_at
        FROM paper_execution_authorizations authz
        JOIN risk_outbox_links link
          ON link.aggregate_kind='paper-authorization'
         AND link.aggregate_id=authz.authorization_id
        JOIN outbox_events event
          ON event.event_id=link.event_id
         AND event.event_type='paper.authorization.issued.v1'
         AND event.aggregate_id=authz.authorization_id
        WHERE authz.namespace='paper'
          AND NOT EXISTS (
            SELECT 1 FROM paper_authorization_attempts attempt
            WHERE attempt.authorization_id=authz.authorization_id)
    """)
    op.execute("""
        CREATE VIEW paper_order_reader_v1 AS
        SELECT orders.order_id, orders.account_id, orders.client_order_id,
          orders.authorization_id, authz.approval_id,
          authz.proposal_hash, authz.risk_decision_hash,
          authz.paper_order_preview_hash,
          orders.symbol, orders.side, orders.order_type, orders.time_in_force,
          orders.quantity, orders.limit_price, orders.filled_quantity,
          orders.status, orders.version
        FROM paper_orders orders
        JOIN paper_execution_authorizations authz
          ON authz.authorization_id=orders.authorization_id
    """)
    op.execute("""
        CREATE VIEW kill_switch_reader_v1 AS
        SELECT state.scope, state.active, state.version,
          state.last_activation_event_id, state.last_recovery_event_id,
          recovery.actor_id AS recovery_actor_id,
          recovery.incident_reference, recovery.observed_at AS recovered_at
        FROM kill_switch_state state
        LEFT JOIN kill_recovery_events recovery
          ON recovery.recovery_event_id=state.last_recovery_event_id
    """)
    op.execute("""
        CREATE VIEW trading_room_analysis_reader_v2 AS
        SELECT run.run_id,
            jsonb_build_object(
              'schema_version','woozoo.analysis-run-view/v1','namespace','paper',
              'symbol',run.symbol,'evidence_id',run.evidence_id,'provider','mock',
              'tool_count',0,'run_id',run.run_id,'status','COMPLETED',
              'report',jsonb_build_object(
                'summary','저장된 모의 언어 모델의 모의투자 분석',
                'confidence','0.50','hold_reasons','[]'::jsonb),
              'proposal_id',run.payload->>'proposal_id',
              'risk_decision_id',decision.decision_id) AS payload
        FROM analysis_runs run
        LEFT JOIN trade_proposals proposal ON proposal.run_id=run.run_id
        LEFT JOIN risk_decisions decision ON decision.proposal_id=proposal.proposal_id
        WHERE run.namespace='paper' AND run.outcome='COMPLETED'
    """)
    op.execute("""
        CREATE VIEW trading_room_risk_reader_v1 AS
        SELECT decision_id,risk_input_digest,decision_hash,verdict,primary_reason,
          ordered_reason_codes,policy_version,proposal_hash,portfolio_snapshot_hash,
          data_state_hash,paper_order_preview_hash,reconciliation_checkpoint_hash,
          kill_switch_version,decision_as_of
        FROM risk_decisions
        WHERE risk_input->>'risk_input_schema_version'='woozoo.risk-input/v3'
          AND risk_input->>'namespace'='paper'
    """)
    op.execute(
        f"""
        CREATE VIEW paper_portfolio_reader_v1 AS
        SELECT balance.account_id,balance.asset,balance.available,balance.held,balance.version
        FROM paper_asset_balances balance
        JOIN paper_accounts account USING(account_id)
        WHERE account.namespace='paper' AND account.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
    """
    )
    op.execute(
        f"""
        CREATE VIEW paper_ledger_entry_reader_v1 AS
        SELECT tx.account_id,entry.transaction_id,entry.line_no,entry.account_code,
          entry.commodity,entry.debit,entry.credit,tx.posted_at
        FROM paper_ledger_transactions tx
        JOIN paper_ledger_entries entry USING(transaction_id)
        WHERE tx.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
    """
    )
    op.execute(
        f"""
        CREATE VIEW paper_health_reader_v1 AS
        SELECT account.account_id,
          CASE WHEN checkpoint.checkpoint_id IS NULL THEN 'MISSING'
               WHEN checkpoint.status<>'HEALTHY' THEN 'FAILED'
               WHEN checkpoint.authority_sequence<>authority.current_sequence THEN 'STALE'
               ELSE 'HEALTHY' END AS reconciliation_status,
          checkpoint.checkpoint_id,checkpoint.created_at AS checkpoint_created_at,
          checkpoint.input_digest AS checkpoint_input_digest,
          CASE
            WHEN EXISTS (
              SELECT 1 FROM paper_ledger_balance_v1 balance
              JOIN paper_ledger_transactions tx USING(transaction_id)
              WHERE tx.account_id=account.account_id AND balance.imbalance<>0)
              OR EXISTS (
                SELECT 1 FROM paper_ledger_transactions tx
                WHERE tx.account_id=account.account_id AND NOT EXISTS (
                  SELECT 1 FROM paper_ledger_entries entry
                  WHERE entry.transaction_id=tx.transaction_id))
            THEN 'UNBALANCED' ELSE 'BALANCED'
          END AS ledger_status,
          (SELECT count(*) FROM paper_ledger_balance_v1 balance
             JOIN paper_ledger_transactions tx USING(transaction_id)
             WHERE tx.account_id=account.account_id AND balance.imbalance<>0)
            AS ledger_imbalance_count,
          checkpoint.authority_sequence AS checkpoint_authority_sequence,
          authority.current_sequence AS current_authority_sequence
        FROM paper_accounts account
        LEFT JOIN LATERAL (
          SELECT latest.checkpoint_id,latest.status,latest.input_digest,latest.created_at,
            latest.authority_sequence
          FROM paper_reconciliation_checkpoints latest
          WHERE latest.account_id=account.account_id
          ORDER BY latest.created_at DESC,latest.checkpoint_id DESC LIMIT 1
        ) checkpoint ON true
        CROSS JOIN LATERAL (
          SELECT COALESCE(max(event.ingestion_sequence),0) AS current_sequence
          FROM paper_outbox_links link JOIN outbox_events event USING(event_id)
          WHERE link.account_id=account.account_id
        ) authority
        WHERE account.namespace='paper' AND account.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
    """
    )
    op.execute(
        """
        CREATE VIEW paper_pending_kill_activations_v1 AS
        SELECT event.ingestion_sequence,kill_event.activation_event_id,event.payload_hash,
          event.occurred_at
        FROM kill_switch_events kill_event
        JOIN risk_outbox_links link
          ON link.aggregate_kind='kill-switch'
         AND link.aggregate_id=kill_event.activation_event_id
        JOIN outbox_events event ON event.event_id=link.event_id
        JOIN kill_switch_state state
          ON state.scope=kill_event.scope AND state.active
         AND state.last_activation_event_id=kill_event.activation_event_id
        WHERE NOT EXISTS (
          SELECT 1 FROM paper_kill_cancel_completions completion
          WHERE completion.activation_event_id=kill_event.activation_event_id)
        ORDER BY event.ingestion_sequence
    """
    )
    op.execute("""
        CREATE VIEW paper_authorization_worker_reader_v1 AS
        SELECT singleton.worker_name,
          CASE WHEN state.worker_name IS NULL THEN 'MISSING'
               WHEN state.status='RUNNING'
                AND (state.heartbeat_at<CURRENT_TIMESTAMP-interval '2 minutes'
                  OR state.heartbeat_at>CURRENT_TIMESTAMP) THEN 'STALE'
               WHEN state.status='RUNNING' THEN 'HEALTHY'
               ELSE state.status END AS status,
          state.instance_id,state.started_at,state.heartbeat_at,state.last_progress_at,
          state.last_result,state.last_error_code,state.stopped_at,
          COALESCE(state.status='RUNNING'
            AND state.heartbeat_at<=CURRENT_TIMESTAMP
            AND state.heartbeat_at>=CURRENT_TIMESTAMP-interval '2 minutes',false) AS ready,
          (SELECT count(*) FROM paper_pending_authorizations_v1) AS pending_authorizations,
          (SELECT count(*) FROM paper_pending_kill_activations_v1) AS pending_kill_activations
        FROM (VALUES ('phase7-paper-authorization'::varchar)) singleton(worker_name)
        LEFT JOIN paper_authorization_worker_state state USING(worker_name)
    """)
    op.execute("""
        CREATE VIEW trading_room_market_reader_v1 AS
        SELECT requested.symbol,
          latest.payload->>'bid_price' AS best_bid,
          latest.payload->>'ask_price' AS best_ask,
          CASE WHEN latest.id IS NULL THEN 'INVALID'
               WHEN latest.quality_status<>'healthy' THEN
                 CASE WHEN latest.quality_status='invalid' THEN 'INVALID' ELSE 'STALE' END
               WHEN latest.received_at<CURRENT_TIMESTAMP-interval '5 seconds' THEN 'STALE'
               ELSE 'HEALTHY' END AS quality
        FROM (VALUES ('BTCUSDT'::varchar),('ETHUSDT'::varchar)) requested(symbol)
        LEFT JOIN LATERAL (
          SELECT event.id,event.payload,event.quality_status,event.received_at
          FROM normalized_market_events event
          WHERE event.symbol=requested.symbol AND event.event_type='book_ticker'
          ORDER BY event.received_at DESC,event.sequence DESC,event.id DESC LIMIT 1
        ) latest ON true
        ORDER BY requested.symbol
    """)
    op.execute(
        f"""
        CREATE VIEW kill_switch_recovery_reader_v1 AS
        SELECT state.scope,state.active,state.version,state.last_activation_event_id,
          state.last_recovery_event_id,
          CASE WHEN NOT state.active THEN 'NOT_ACTIVE'
               WHEN completion.activation_event_id IS NULL THEN 'INCOMPLETE'
               ELSE 'COMPLETE' END AS cancellation_status,
          completion.completed_at AS cancellation_completed_at,
          open_orders.open_order_count,
          CASE WHEN data_health.healthy THEN 'HEALTHY' ELSE 'UNHEALTHY'
            END AS data_status,
          CASE WHEN checkpoint.checkpoint_id IS NULL THEN 'MISSING'
               WHEN completion.activation_event_id IS NOT NULL
                AND checkpoint.status='HEALTHY'
                AND checkpoint.mismatch_codes='[]'::jsonb
                AND checkpoint.created_at>completion.completed_at
                AND checkpoint.input_digest=completion.state_digest
               THEN 'HEALTHY' ELSE 'UNHEALTHY'
            END AS reconciliation_status,
          health.ledger_status,
          worker.status AS worker_status,worker.heartbeat_at AS worker_heartbeat_at,
          worker.last_progress_at AS worker_last_progress_at,
          worker.last_error_code AS worker_last_error_code,worker.ready AS worker_ready,
          COALESCE((state.active
            AND completion.activation_event_id IS NOT NULL
            AND open_orders.open_order_count=0
            AND data_health.healthy
            AND checkpoint.status='HEALTHY'
            AND checkpoint.mismatch_codes='[]'::jsonb
            AND checkpoint.created_at>completion.completed_at
            AND checkpoint.input_digest=completion.state_digest
            AND health.reconciliation_status='HEALTHY'
            AND health.ledger_status='BALANCED'
            AND worker.ready),false) AS recovery_allowed
        FROM kill_switch_state state
        LEFT JOIN paper_kill_cancel_completions completion
          ON completion.activation_event_id=state.last_activation_event_id
         AND completion.paper_account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
        CROSS JOIN LATERAL (
          SELECT count(*) AS open_order_count FROM paper_orders orders
          WHERE orders.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
            AND orders.status IN ('OPEN','PARTIALLY_FILLED')
        ) open_orders
        CROSS JOIN LATERAL (
          SELECT count(*)=2 AS healthy FROM (
            SELECT DISTINCT ON (event.symbol) event.symbol,event.received_at,
              event.quality_status,event.id,event.payload
            FROM normalized_market_events event
            WHERE event.symbol IN ('BTCUSDT','ETHUSDT')
              AND event.event_type='book_ticker'
            ORDER BY event.symbol,event.received_at DESC,event.sequence DESC,event.id DESC
          ) latest
          WHERE latest.quality_status='healthy'
            AND latest.received_at<=CURRENT_TIMESTAMP
            AND latest.received_at>=CURRENT_TIMESTAMP-interval '5 seconds'
            AND latest.payload ?& array['bid_price','ask_price']
        ) data_health
        LEFT JOIN LATERAL (
          SELECT latest.checkpoint_id,latest.status,latest.mismatch_codes,
            latest.input_digest,latest.created_at
          FROM paper_reconciliation_checkpoints latest
          WHERE latest.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
          ORDER BY latest.created_at DESC,latest.checkpoint_id DESC LIMIT 1
        ) checkpoint ON true
        JOIN paper_health_reader_v1 health
          ON health.account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
        CROSS JOIN paper_authorization_worker_reader_v1 worker
        WHERE state.scope='paper-global'
    """
    )
    op.execute(r"""
        CREATE FUNCTION trading_room_public_audit_data_v1(value jsonb) RETURNS jsonb AS $$
        DECLARE result jsonb;
        BEGIN
          CASE jsonb_typeof(value)
            WHEN 'object' THEN
              SELECT COALESCE(jsonb_object_agg(entry.key,
                       trading_room_public_audit_data_v1(entry.value)),'{}'::jsonb)
                INTO result FROM jsonb_each(value) entry
                WHERE lower(entry.key) !~ '(nonce|binding|session|csrf|origin)';
            WHEN 'array' THEN
              SELECT COALESCE(jsonb_agg(
                       trading_room_public_audit_data_v1(entry.value)
                       ORDER BY entry.ordinality),'[]'::jsonb)
                INTO result FROM jsonb_array_elements(value)
                  WITH ORDINALITY entry(value,ordinality);
            ELSE result:=value;
          END CASE;
          RETURN result;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE STRICT
    """)
    op.execute("REVOKE ALL ON FUNCTION trading_room_public_audit_data_v1(jsonb) FROM PUBLIC")
    op.execute("""
        CREATE FUNCTION project_trading_room_audit_event_v1() RETURNS trigger AS $$
        BEGIN
          IF NEW.event_type LIKE 'analysis.%'
             OR NEW.event_type LIKE 'trade.proposal.%'
             OR NEW.event_type LIKE 'risk.%'
             OR NEW.event_type LIKE 'paper.%'
             OR NEW.event_type LIKE 'kill-switch.%'
             OR NEW.event_type LIKE 'ledger.%'
          THEN
            PERFORM pg_advisory_xact_lock(
              hashtextextended('trading-room-audit-projection-v1',0));
            INSERT INTO trading_room_audit_projection(event_id) VALUES (NEW.event_id)
            ON CONFLICT (event_id) DO NOTHING;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION project_trading_room_audit_event_v1() FROM PUBLIC")
    op.execute("""
        CREATE TRIGGER outbox_events_trading_room_audit_projection_v1
        AFTER INSERT ON outbox_events
        FOR EACH ROW EXECUTE FUNCTION project_trading_room_audit_event_v1()
    """)
    op.execute("""
        SELECT pg_advisory_xact_lock(
          hashtextextended('trading-room-audit-projection-v1',0))
    """)
    op.execute("""
        INSERT INTO trading_room_audit_projection(event_id)
        SELECT event.event_id FROM outbox_events event
        WHERE event.event_type LIKE 'analysis.%'
           OR event.event_type LIKE 'trade.proposal.%'
           OR event.event_type LIKE 'risk.%'
           OR event.event_type LIKE 'paper.%'
           OR event.event_type LIKE 'kill-switch.%'
           OR event.event_type LIKE 'ledger.%'
        ORDER BY event.ingestion_sequence
        ON CONFLICT (event_id) DO NOTHING
    """)
    op.execute("""
        CREATE VIEW trading_room_audit_reader_v1 AS
        SELECT projection.sequence,
          event.event_id,event.event_type,event.occurred_at,
          event.payload->>'producer' AS producer,
          NULLIF(event.payload#>>'{data,actor_id}','') AS actor_id,
          trading_room_public_audit_data_v1(
            COALESCE(event.payload->'data','{}'::jsonb)) AS data
        FROM trading_room_audit_projection projection
        JOIN outbox_events event USING(event_id)
    """)

    for view in (
        "paper_approval_view_v1",
        "paper_authorization_view_v1",
        "paper_pending_authorizations_v1",
        "paper_order_reader_v1",
        "kill_switch_reader_v1",
        "trading_room_analysis_reader_v2",
        "trading_room_risk_reader_v1",
        "paper_portfolio_reader_v1",
        "paper_ledger_entry_reader_v1",
        "paper_health_reader_v1",
        "paper_pending_kill_activations_v1",
        "paper_authorization_worker_reader_v1",
        "trading_room_market_reader_v1",
        "kill_switch_recovery_reader_v1",
        "trading_room_audit_reader_v1",
    ):
        op.execute(f"REVOKE ALL ON {view} FROM PUBLIC")
    for table in (
        "local_operators",
        "operator_sessions",
        "session_csrf_tokens",
        "paper_approvals",
        "paper_approval_revocations",
        "risk_approval_command_receipts",
        "risk_approval_revocation_command_receipts",
        "paper_cancel_command_receipts",
        "paper_execution_authorizations",
        "kill_recovery_events",
        "risk_kill_recovery_command_receipts",
        "paper_kill_cancel_completions",
        "paper_authorization_worker_state",
        "agent_analysis_command_receipts",
        "trading_room_audit_projection",
    ):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")

    op.execute("""
        DO $$
        DECLARE role_created boolean := false; schema_usage boolean := false; role_oid oid;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_api') THEN
            CREATE ROLE woozoo_control_api LOGIN;
            role_created := true;
          ELSE
            SELECT oid INTO role_oid FROM pg_roles WHERE rolname='woozoo_control_api';
            SELECT has_schema_privilege(role_oid,'public','USAGE') INTO schema_usage;
          END IF;
          INSERT INTO trading_room_migration_metadata
            (migration_revision,control_role_created,control_schema_usage_preexisting)
          VALUES ('20260720_0007',role_created,schema_usage);
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_control_api")
    op.execute(
        "GRANT SELECT, INSERT ON local_operators, operator_sessions, session_csrf_tokens "
        "TO woozoo_control_api"
    )
    op.execute(
        "GRANT UPDATE (last_seen_at,idle_expires_at,revoked_at) ON operator_sessions "
        "TO woozoo_control_api"
    )
    op.execute("GRANT UPDATE (consumed_at) ON session_csrf_tokens TO woozoo_control_api")
    op.execute(
        "GRANT SELECT ON paper_approval_view_v1, paper_authorization_view_v1, "
        "paper_order_reader_v1, paper_ledger_balance_v1, kill_switch_reader_v1, "
        "trading_room_analysis_reader_v2, trading_room_risk_reader_v1, "
        "paper_portfolio_reader_v1, paper_ledger_entry_reader_v1, "
        "paper_health_reader_v1, kill_switch_recovery_reader_v1, "
        "paper_authorization_worker_reader_v1, trading_room_market_reader_v1, "
        "trading_room_audit_reader_v1 "
        "TO woozoo_control_api"
    )
    op.execute(
        "GRANT SELECT ON paper_health_reader_v1, kill_switch_recovery_reader_v1 "
        "TO woozoo_control_reader"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION trading_room_public_audit_data_v1(jsonb) "
        "TO woozoo_control_api, woozoo_control_reader"
    )
    op.execute(
        "GRANT SELECT ON trade_proposals, risk_decisions, paper_accounts, paper_approvals, "
        "paper_approval_revocations, paper_execution_authorizations, kill_recovery_events, "
        "risk_kill_recovery_command_receipts, paper_kill_cancel_completions "
        "TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT SELECT, INSERT ON risk_approval_command_receipts, "
        "risk_approval_revocation_command_receipts TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT UPDATE (active,version,last_activation_event_id,last_recovery_event_id) "
        "ON kill_switch_state TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION persist_risk_decision_v1(jsonb,jsonb) TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION load_authoritative_risk_context_v1("
        "varchar,varchar,timestamptz) TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION issue_paper_approval_v1(varchar,varchar,varchar,varchar,"
        "bigint,varchar,varchar,varchar,varchar,varchar,varchar,varchar,timestamptz) "
        "TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION revoke_paper_approval_v1(varchar,varchar,varchar,bigint,"
        "varchar,varchar,varchar,varchar,varchar,varchar,timestamptz) "
        "TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION recover_kill_switch_v1(varchar,varchar,bigint,varchar,"
        "varchar,varchar,varchar,varchar,varchar,varchar,timestamptz) "
        "TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT SELECT ON paper_execution_authorizations, paper_approvals, "
        "paper_approval_revocations, evidence_snapshots, normalized_market_events "
        "TO woozoo_paper_engine"
    )
    op.execute("GRANT SELECT, INSERT ON paper_kill_cancel_completions TO woozoo_paper_engine")
    op.execute(
        "GRANT EXECUTE ON FUNCTION paper_lock_execution_authorization_v1(varchar) "
        "TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION paper_validate_kill_activation_v1(varchar,varchar) "
        "TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION paper_recorded_book_market_is_current_v1(varchar) "
        "TO woozoo_paper_engine"
    )
    op.execute("GRANT SELECT ON paper_pending_authorizations_v1 TO woozoo_paper_engine")
    op.execute("GRANT SELECT ON paper_pending_kill_activations_v1 TO woozoo_paper_engine")
    op.execute("GRANT SELECT, INSERT ON paper_authorization_worker_state TO woozoo_paper_engine")
    op.execute(
        "GRANT UPDATE (instance_id,status,started_at,heartbeat_at,last_progress_at,"
        "last_result,last_error_code,stopped_at) ON paper_authorization_worker_state "
        "TO woozoo_paper_engine"
    )
    op.execute("GRANT SELECT, INSERT ON paper_cancel_command_receipts TO woozoo_paper_engine")
    op.execute(
        "GRANT SELECT, INSERT ON agent_analysis_command_receipts TO woozoo_agent_orchestrator"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER outbox_events_trading_room_audit_projection_v1 ON outbox_events")
    op.execute("DROP FUNCTION project_trading_room_audit_event_v1()")
    op.execute("DROP TRIGGER paper_cancel_receipt_binding ON paper_cancel_command_receipts")
    op.execute("DROP FUNCTION enforce_paper_cancel_receipt_v1()")
    op.execute(
        "DROP FUNCTION append_paper_outbox(varchar,varchar,jsonb,varchar,timestamptz,"
        "varchar,varchar,bigint)"
    )
    op.execute(
        "ALTER FUNCTION append_paper_outbox_phase4_v1(varchar,varchar,jsonb,varchar,"
        "timestamptz,varchar,varchar,bigint) RENAME TO append_paper_outbox"
    )
    op.execute("DROP FUNCTION phase7_canonical_json(jsonb)")
    op.execute(
        "DROP FUNCTION IF EXISTS load_authoritative_risk_context_v1(varchar,varchar,timestamptz)"
    )
    op.execute(
        "DROP FUNCTION recover_kill_switch_v1(varchar,varchar,bigint,varchar,varchar,"
        "varchar,varchar,varchar,varchar,varchar,timestamptz)"
    )
    op.execute(
        "DROP FUNCTION revoke_paper_approval_v1(varchar,varchar,varchar,bigint,varchar,"
        "varchar,varchar,varchar,varchar,varchar,timestamptz)"
    )
    op.execute("DROP FUNCTION paper_lock_execution_authorization_v1(varchar)")
    op.execute("DROP FUNCTION paper_validate_kill_activation_v1(varchar,varchar)")
    op.execute("DROP FUNCTION paper_recorded_book_market_is_current_v1(varchar)")
    op.execute(
        "DROP FUNCTION issue_paper_approval_v1(varchar,varchar,varchar,varchar,bigint,"
        "varchar,varchar,varchar,varchar,varchar,varchar,varchar,timestamptz)"
    )
    op.execute("DROP FUNCTION persist_risk_decision_v1(jsonb,jsonb)")
    op.execute(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM paper_approvals)
             OR EXISTS (SELECT 1 FROM paper_approval_revocations)
             OR EXISTS (SELECT 1 FROM paper_execution_authorizations)
             OR EXISTS (SELECT 1 FROM kill_recovery_events)
             OR EXISTS (SELECT 1 FROM risk_kill_recovery_command_receipts)
             OR EXISTS (SELECT 1 FROM paper_kill_cancel_completions)
             OR EXISTS (SELECT 1 FROM risk_approval_command_receipts)
             OR EXISTS (SELECT 1 FROM risk_approval_revocation_command_receipts)
             OR EXISTS (SELECT 1 FROM paper_cancel_command_receipts)
             OR EXISTS (SELECT 1 FROM paper_authorization_attempts WHERE namespace='paper')
             OR EXISTS (SELECT 1 FROM paper_accounts WHERE namespace='paper'
                        AND account_id<>'{PAPER_DEFAULT_ACCOUNT_ID}')
             OR EXISTS (SELECT 1 FROM paper_broker_inputs
                        WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}')
             OR EXISTS (SELECT 1 FROM paper_reconciliation_checkpoints
                        WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}')
             OR EXISTS (SELECT 1 FROM paper_ledger_transactions
                        WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}'
                          AND transaction_id<>'{PAPER_BOOTSTRAP_TRANSACTION_ID}')
             OR EXISTS (
               SELECT 1 FROM paper_asset_balances
               WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}' AND NOT (
                 (asset IN ('BTC','ETH') AND available=0 AND held=0 AND version=0)
                 OR (asset='USDT' AND available=10000 AND held=0 AND version=0)))
             OR EXISTS (SELECT 1 FROM analysis_runs WHERE namespace='paper')
          THEN
            RAISE EXCEPTION 'Phase 7 downgrade blocked: immutable production history exists';
          END IF;
        END $$
    """)
    op.execute("""
        CREATE TEMP TABLE phase7_role_cleanup AS
        SELECT control_role_created,control_schema_usage_preexisting
        FROM trading_room_migration_metadata WHERE migration_revision='20260720_0007'
    """)
    op.execute("""
        REVOKE SELECT ON paper_execution_authorizations, paper_approvals,
          paper_approval_revocations, evidence_snapshots, normalized_market_events
          FROM woozoo_paper_engine;
        REVOKE SELECT ON paper_pending_authorizations_v1 FROM woozoo_paper_engine;
        REVOKE SELECT ON paper_pending_kill_activations_v1 FROM woozoo_paper_engine;
        REVOKE SELECT, INSERT, UPDATE ON paper_authorization_worker_state
          FROM woozoo_paper_engine;
        REVOKE SELECT, INSERT ON paper_cancel_command_receipts FROM woozoo_paper_engine;
        REVOKE SELECT, INSERT ON paper_kill_cancel_completions FROM woozoo_paper_engine;
        REVOKE SELECT, INSERT ON agent_analysis_command_receipts
          FROM woozoo_agent_orchestrator;
        REVOKE SELECT, INSERT ON paper_approvals, paper_approval_revocations,
          paper_execution_authorizations, kill_recovery_events,
          risk_kill_recovery_command_receipts, paper_kill_cancel_completions
          FROM woozoo_risk_engine;
        REVOKE SELECT, INSERT ON risk_approval_command_receipts,
          risk_approval_revocation_command_receipts FROM woozoo_risk_engine;
        REVOKE SELECT ON trade_proposals, paper_accounts FROM woozoo_risk_engine;
        REVOKE UPDATE (active,version,last_activation_event_id,last_recovery_event_id)
          ON kill_switch_state FROM woozoo_risk_engine;
    """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_api') THEN
            REVOKE ALL ON local_operators, operator_sessions, session_csrf_tokens
              FROM woozoo_control_api;
            REVOKE ALL ON paper_approval_view_v1, paper_authorization_view_v1,
              paper_order_reader_v1, paper_ledger_balance_v1, kill_switch_reader_v1,
              trading_room_analysis_reader_v2, trading_room_risk_reader_v1,
              paper_portfolio_reader_v1, paper_ledger_entry_reader_v1,
              paper_health_reader_v1, paper_authorization_worker_reader_v1,
              trading_room_market_reader_v1, kill_switch_recovery_reader_v1,
              trading_room_audit_reader_v1
              FROM woozoo_control_api;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_reader') THEN
            REVOKE SELECT ON paper_health_reader_v1, kill_switch_recovery_reader_v1
              FROM woozoo_control_reader;
            REVOKE EXECUTE ON FUNCTION trading_room_public_audit_data_v1(jsonb)
              FROM woozoo_control_reader;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_api') THEN
            REVOKE EXECUTE ON FUNCTION trading_room_public_audit_data_v1(jsonb)
              FROM woozoo_control_api;
          END IF;
        END $$
    """)
    for view in (
        "trading_room_audit_reader_v1",
        "kill_switch_recovery_reader_v1",
        "trading_room_market_reader_v1",
        "paper_authorization_worker_reader_v1",
        "paper_pending_kill_activations_v1",
        "paper_health_reader_v1",
        "paper_ledger_entry_reader_v1",
        "paper_portfolio_reader_v1",
        "trading_room_risk_reader_v1",
        "trading_room_analysis_reader_v2",
        "kill_switch_reader_v1",
        "paper_order_reader_v1",
        "paper_authorization_view_v1",
        "paper_pending_authorizations_v1",
        "paper_approval_view_v1",
    ):
        op.execute(f"DROP VIEW {view}")
    op.execute("DROP FUNCTION trading_room_public_audit_data_v1(jsonb)")
    op.execute(
        "DROP TRIGGER paper_reconciliation_authority_sequence_v1 "
        "ON paper_reconciliation_checkpoints"
    )
    op.execute("DROP FUNCTION stamp_paper_reconciliation_authority_sequence_v1()")

    op.execute("DROP TRIGGER paper_kill_cancel_completion_binding ON paper_kill_cancel_completions")
    op.execute("DROP FUNCTION enforce_paper_kill_cancel_completion_v1()")

    for table in (
        "kill_recovery_events",
        "risk_kill_recovery_command_receipts",
        "kill_switch_state",
    ):
        op.execute(f"DROP TRIGGER {table}_phase7_recovery_consistency ON {table}")
    op.execute("DROP FUNCTION assert_kill_recovery_consistency()")
    op.execute("DROP TRIGGER kill_switch_state_phase7_transition ON kill_switch_state")
    op.execute("DROP FUNCTION enforce_kill_state_phase7_transition()")
    op.drop_constraint("ck_kill_state_phase7", "kill_switch_state", type_="check")
    op.drop_constraint("fk_kill_state_phase7_recovery", "kill_switch_state", type_="foreignkey")
    op.drop_column("kill_switch_state", "last_recovery_event_id")
    op.create_check_constraint(
        "ck_kill_state_monotonic",
        "kill_switch_state",
        "(active=false AND version=0 AND last_activation_event_id IS NULL) OR "
        "(active=true AND version>0 AND last_activation_event_id IS NOT NULL)",
    )
    op.execute("""
        CREATE TRIGGER kill_switch_state_activation_only
        BEFORE UPDATE ON kill_switch_state FOR EACH ROW
        EXECUTE FUNCTION enforce_kill_state_activation_only()
    """)

    op.execute(
        "DROP TRIGGER paper_authorization_attempt_phase7_binding ON paper_authorization_attempts"
    )
    op.execute("DROP FUNCTION enforce_phase7_attempt_binding()")
    op.execute("DROP TRIGGER paper_broker_input_phase7_binding ON paper_broker_inputs")
    op.execute("DROP FUNCTION enforce_phase7_broker_input_binding()")
    op.execute(
        "DROP TRIGGER paper_execution_authorization_binding ON paper_execution_authorizations"
    )
    op.execute("DROP FUNCTION enforce_phase7_authorization_binding()")
    op.execute("DROP TRIGGER paper_approval_phase7_binding ON paper_approvals")
    op.execute("DROP FUNCTION enforce_phase7_approval_binding()")
    op.execute(
        "DROP TRIGGER paper_approval_revocation_phase7_binding ON paper_approval_revocations"
    )
    op.execute("DROP FUNCTION enforce_phase7_revocation_binding()")
    op.execute("DROP TRIGGER kill_recovery_actor_phase7_binding ON kill_recovery_events")
    op.execute("DROP FUNCTION enforce_phase7_recovery_actor_binding()")
    op.execute("DROP TRIGGER risk_approval_receipt_binding ON risk_approval_command_receipts")
    op.execute("DROP FUNCTION enforce_risk_approval_receipt()")
    op.execute(
        "DROP TRIGGER risk_approval_revocation_receipt_binding "
        "ON risk_approval_revocation_command_receipts"
    )
    op.execute("DROP FUNCTION enforce_risk_approval_revocation_receipt()")
    for table in (
        "paper_approvals",
        "paper_approval_revocations",
        "paper_execution_authorizations",
        "kill_recovery_events",
        "risk_outbox_links",
        "outbox_events",
    ):
        op.execute(f"DROP TRIGGER {table}_phase7_risk_outbox_consistency ON {table}")
    op.execute("DROP FUNCTION assert_phase7_risk_outbox_consistency()")
    op.execute("""
        DO $$ DECLARE saved record; BEGIN
          FOR saved IN SELECT function_definition FROM phase7_function_backup ORDER BY function_name
          LOOP EXECUTE saved.function_definition; END LOOP;
        END $$
    """)

    op.drop_constraint("ck_phase7_analysis_run_namespace", "analysis_runs", type_="check")
    op.create_check_constraint("ck_analysis_run_namespace", "analysis_runs", "namespace='test'")
    op.drop_constraint("ck_phase7_risk_outbox_kind", "risk_outbox_links", type_="check")
    op.create_check_constraint(
        "ck_risk_outbox_kind",
        "risk_outbox_links",
        "aggregate_kind IN ('risk-decision','kill-switch')",
    )
    op.drop_constraint(
        "ck_phase7_attempt_authorization_link", "paper_authorization_attempts", type_="check"
    )
    op.drop_constraint(
        "fk_paper_attempt_phase7_authorization",
        "paper_authorization_attempts",
        type_="foreignkey",
    )
    op.drop_column("paper_authorization_attempts", "paper_execution_authorization_id")
    op.execute("ALTER TABLE paper_ledger_entries DISABLE TRIGGER USER")
    op.execute("ALTER TABLE paper_ledger_transactions DISABLE TRIGGER USER")
    op.execute("ALTER TABLE paper_asset_balances DISABLE TRIGGER USER")
    op.execute("ALTER TABLE paper_accounts DISABLE TRIGGER USER")
    op.execute(
        f"DELETE FROM paper_ledger_entries WHERE transaction_id='{PAPER_BOOTSTRAP_TRANSACTION_ID}'"
    )
    op.execute(
        f"DELETE FROM paper_ledger_transactions "
        f"WHERE transaction_id='{PAPER_BOOTSTRAP_TRANSACTION_ID}'"
    )
    op.execute(f"DELETE FROM paper_asset_balances WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}'")
    op.execute(f"DELETE FROM paper_accounts WHERE account_id='{PAPER_DEFAULT_ACCOUNT_ID}'")
    op.execute("ALTER TABLE paper_accounts ENABLE TRIGGER USER")
    op.execute("ALTER TABLE paper_asset_balances ENABLE TRIGGER USER")
    op.execute("ALTER TABLE paper_ledger_transactions ENABLE TRIGGER USER")
    op.execute("ALTER TABLE paper_ledger_entries ENABLE TRIGGER USER")
    op.drop_constraint("ck_phase7_broker_input_liquidity", "paper_broker_inputs", type_="check")
    op.drop_constraint("ck_phase7_broker_input_kind", "paper_broker_inputs", type_="check")
    op.drop_constraint(
        "fk_paper_input_phase7_authorization", "paper_broker_inputs", type_="foreignkey"
    )
    op.drop_column("paper_broker_inputs", "paper_execution_authorization_id")
    op.create_check_constraint(
        "ck_p4_broker_input_kind",
        "paper_broker_inputs",
        "source_kind IN ('TEST_COMMAND','RECORDED_BOOK')",
    )
    op.create_check_constraint(
        "ck_paper_input_liquidity",
        "paper_broker_inputs",
        "(source_kind='TEST_COMMAND' AND available_quantity IS NULL "
        "AND symbol IS NULL AND best_bid IS NULL AND best_ask IS NULL) OR "
        "(source_kind='RECORDED_BOOK' AND available_quantity>0 "
        "AND symbol IN ('BTCUSDT','ETHUSDT') AND best_bid>0 AND best_ask>0 "
        "AND best_bid<=best_ask)",
    )
    op.drop_constraint(
        "ck_phase7_authorization_namespace", "paper_authorization_attempts", type_="check"
    )
    op.create_check_constraint(
        "ck_p4_authorization_test_only", "paper_authorization_attempts", "namespace='test'"
    )
    op.drop_constraint("ck_phase7_paper_account_namespace", "paper_accounts", type_="check")
    op.create_check_constraint(
        "ck_p4_paper_account_test_only", "paper_accounts", "namespace='test'"
    )

    for table in (
        "paper_kill_cancel_completions",
        "paper_authorization_worker_state",
        "trading_room_audit_projection",
        "agent_analysis_command_receipts",
        "risk_kill_recovery_command_receipts",
        "kill_recovery_events",
        "paper_execution_authorizations",
        "risk_approval_revocation_command_receipts",
        "paper_cancel_command_receipts",
        "risk_approval_command_receipts",
        "paper_approval_revocations",
        "paper_approvals",
        "session_csrf_tokens",
        "operator_sessions",
        "local_operators",
        "phase7_function_backup",
        "trading_room_migration_metadata",
    ):
        op.drop_table(table)
    op.drop_column("paper_reconciliation_checkpoints", "authority_sequence")
    op.drop_constraint("uq_outbox_events_ingestion_sequence", "outbox_events", type_="unique")
    op.drop_column("outbox_events", "ingestion_sequence")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_api')
             AND NOT EXISTS (
               SELECT 1 FROM phase7_role_cleanup WHERE control_schema_usage_preexisting
             ) THEN
            REVOKE USAGE ON SCHEMA public FROM woozoo_control_api;
          END IF;
          IF EXISTS (SELECT 1 FROM phase7_role_cleanup WHERE control_role_created) THEN
            DROP ROLE woozoo_control_api;
          END IF;
        END $$
    """)
