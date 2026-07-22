"""Create isolated Phase 8 Spot Testnet execution and Gateway authority."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260722_0008"
down_revision = "20260720_0007"
branch_labels = None
depends_on = None

HASH_CHECK = " ~ '^[a-f0-9]{64}$'"
CLIENT_ID_CHECK = "client_order_id ~ '^wz8-[a-f0-9]{32}$'"
DECIMAL_POLICY = "NUMERIC(38,18)"
LOCAL_TESTNET_ACCOUNT_ID = "39c5f6b40a6b89656775035440719d2c450999a45d7df4a5baa041e0985741e2"
INITIAL_TESTNET_GENERATION_ID = "fe52fe33409fccd9698bb6c0a42bc097bbaa90bd657ea8fda108dadabd40d875"


def _append_only(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_risk_history_mutation()
    """)


def _hashes(*columns: str) -> str:
    return " AND ".join(column + HASH_CHECK for column in columns)


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_testnet_execution') THEN
            CREATE ROLE woozoo_testnet_execution LOGIN;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname='woozoo_spot_testnet_gateway'
          ) THEN
            CREATE ROLE woozoo_spot_testnet_gateway LOGIN;
          END IF;
        END $$
    """)
    op.execute(
        "GRANT USAGE ON SCHEMA public TO woozoo_testnet_execution, woozoo_spot_testnet_gateway"
    )

    op.create_table(
        "testnet_accounts",
        sa.Column("account_binding_id", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("account_label", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "account_binding_id" + HASH_CHECK,
            name="ck_testnet_account_binding_hash",
        ),
        sa.CheckConstraint(
            "environment='BINANCE_SPOT_TESTNET'",
            name="ck_testnet_account_environment",
        ),
    )
    _append_only("testnet_accounts")
    op.create_table(
        "testnet_account_generations",
        sa.Column("generation_id", sa.String(64), primary_key=True),
        sa.Column("account_binding_id", sa.String(64), nullable=False),
        sa.Column("generation_number", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("opening_snapshot_digest", sa.String(64), nullable=True),
        sa.Column("operator_confirmation_digest", sa.String(64), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_binding_id"], ["testnet_accounts.account_binding_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "account_binding_id", "generation_number", name="uq_testnet_account_generation"
        ),
        sa.CheckConstraint(
            _hashes("generation_id", "account_binding_id"),
            name="ck_testnet_generation_hashes",
        ),
        sa.CheckConstraint(
            "generation_number>=1 AND version>=1",
            name="ck_testnet_generation_versions",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_RECONCILIATION','ACTIVE','RESET_SUSPECTED',"
            "'AWAITING_OPERATOR_CONFIRMATION','RETIRED')",
            name="ck_testnet_generation_status",
        ),
    )
    # This is a local, non-secret account binding.  It deliberately contains no
    # credential identity and begins in reconciliation HOLD.  Seeding it makes
    # the activation/preflight path reachable without a privileged manual SQL
    # bootstrap while preserving default-off network behavior.
    op.execute(f"""
        INSERT INTO testnet_accounts(account_binding_id,environment,account_label,created_at)
        VALUES ('{LOCAL_TESTNET_ACCOUNT_ID}','BINANCE_SPOT_TESTNET',
                '로컬 Spot Testnet 계정',CURRENT_TIMESTAMP)
    """)
    op.execute(f"""
        INSERT INTO testnet_account_generations(
          generation_id,account_binding_id,generation_number,status,
          opening_snapshot_digest,operator_confirmation_digest,opened_at,closed_at,version)
        VALUES ('{INITIAL_TESTNET_GENERATION_ID}','{LOCAL_TESTNET_ACCOUNT_ID}',1,
                'PENDING_RECONCILIATION',NULL,NULL,CURRENT_TIMESTAMP,NULL,1)
    """)
    op.create_table(
        "testnet_activations",
        sa.Column("activation_id", sa.String(64), primary_key=True),
        sa.Column("account_binding_id", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("gateway_instance_id", sa.String(64), nullable=False),
        sa.Column("build_digest", sa.String(64), nullable=False),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column("allowlist_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(
            ["account_binding_id"], ["testnet_accounts.account_binding_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"], ["session_csrf_tokens.csrf_token_digest"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "activation_id",
                "account_binding_id",
                "generation_id",
                "gateway_instance_id",
                "build_digest",
                "configuration_digest",
                "allowlist_digest",
                "session_digest",
                "csrf_token_digest",
                "origin_hash",
                "payload_hash",
            ),
            name="ck_testnet_activation_hashes",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','ACTIVE','EXPIRED','REVOKED') AND version>=1",
            name="ck_testnet_activation_status_version",
        ),
        sa.CheckConstraint(
            "expires_at>activated_at AND expires_at<=activated_at + interval '30 minutes'",
            name="ck_testnet_activation_ttl",
        ),
    )
    _append_only("testnet_activations")
    op.create_table(
        "testnet_activation_revocations",
        sa.Column("revocation_id", sa.String(64), primary_key=True),
        sa.Column("activation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(
            ["activation_id"], ["testnet_activations.activation_id"], ondelete="RESTRICT"
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
            _hashes(
                "revocation_id",
                "activation_id",
                "session_digest",
                "csrf_token_digest",
                "origin_hash",
                "payload_hash",
            ),
            name="ck_testnet_activation_revocation_hashes",
        ),
    )
    _append_only("testnet_activation_revocations")
    op.create_table(
        "testnet_safety_state",
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("last_event_id", sa.String(64), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope='testnet-global'", name="ck_testnet_safety_scope"),
        sa.CheckConstraint("version>=0", name="ck_testnet_safety_version"),
        sa.CheckConstraint(
            "reason_code IN ('DEFAULT_OFF','MANUAL_STOP','RECONCILIATION_FAILURE',"
            "'SUBMISSION_UNKNOWN','USER_DATA_GAP','RESET_SUSPECTED','GATEWAY_DRIFT',"
            "'LEDGER_IMBALANCE','INVARIANT_FAILURE')",
            name="ck_testnet_safety_reason",
        ),
    )
    op.execute("""
        INSERT INTO testnet_safety_state(scope,active,version,last_event_id,reason_code,updated_at)
        VALUES ('testnet-global',true,0,NULL,'DEFAULT_OFF',CURRENT_TIMESTAMP)
    """)

    op.create_table(
        "testnet_operator_intents",
        sa.Column("intent_id", sa.String(64), primary_key=True),
        sa.Column("intent_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
            _hashes(
                "intent_id",
                "request_digest",
                "session_digest",
                "csrf_token_digest",
                "origin_hash",
            ),
            name="ck_testnet_operator_intent_hashes",
        ),
        sa.CheckConstraint(
            "target_id IS NULL OR target_id" + HASH_CHECK,
            name="ck_testnet_operator_intent_target",
        ),
        sa.CheckConstraint(
            "intent_kind IN ('ACTIVATE','DEACTIVATE','APPROVE','REJECT','REVOKE',"
            "'CONFIRM_RESET','CANCEL') AND expected_version>=0",
            name="ck_testnet_operator_intent_kind",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_testnet_operator_intent_idempotency",
        ),
    )
    _append_only("testnet_operator_intents")

    op.create_table(
        "testnet_operator_intent_results",
        sa.Column("intent_id", sa.String(64), primary_key=True),
        sa.Column("result_id", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["testnet_operator_intents.intent_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes("intent_id", "result_id"), name="ck_testnet_operator_result_hashes"
        ),
        sa.CheckConstraint(
            "status IN ('APPLIED','REJECTED','BLOCKED')",
            name="ck_testnet_operator_result_status",
        ),
    )
    _append_only("testnet_operator_intent_results")

    op.create_table(
        "testnet_domain_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _hashes("event_id", "aggregate_id", "payload_hash"),
            name="ck_testnet_domain_event_hashes",
        ),
        sa.CheckConstraint(
            "event_type ~ '^testnet\\.[a-z0-9.-]+\\.v1$'",
            name="ck_testnet_domain_event_type",
        ),
    )
    _append_only("testnet_domain_events")
    op.create_table(
        "testnet_outbox",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["testnet_domain_events.event_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("topic='testnet-domain-v1'", name="ck_testnet_outbox_topic"),
        sa.CheckConstraint(_hashes("event_id", "payload_hash"), name="ck_testnet_outbox_hashes"),
    )
    _append_only("testnet_outbox")

    op.create_table(
        "testnet_order_previews",
        sa.Column("preview_digest", sa.String(64), primary_key=True),
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("limit_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False, unique=True),
        sa.Column("preview", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes("preview_digest", "proposal_id", "proposal_hash", "generation_id"),
            name="ck_testnet_preview_hashes",
        ),
        sa.CheckConstraint(CLIENT_ID_CHECK, name="ck_testnet_preview_client_id"),
        sa.CheckConstraint(
            "symbol IN ('BTCUSDT','ETHUSDT') AND side IN ('BUY','SELL') "
            "AND quantity>0 AND limit_price>0",
            name="ck_testnet_preview_order",
        ),
    )
    _append_only("testnet_order_previews")
    op.create_table(
        "testnet_risk_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("decision_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("risk_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("risk_input", postgresql.JSONB(), nullable=False),
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("preview_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("ordered_reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["preview_digest"], ["testnet_order_previews.preview_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "decision_id",
                "decision_hash",
                "risk_input_digest",
                "proposal_id",
                "preview_digest",
                "generation_id",
            ),
            name="ck_testnet_risk_hashes",
        ),
        sa.CheckConstraint(
            "verdict IN ('ALLOWED','DENIED','ERROR')",
            name="ck_testnet_risk_verdict",
        ),
    )
    _append_only("testnet_risk_decisions")
    op.create_table(
        "testnet_risk_materialization_results",
        sa.Column("proposal_id", sa.String(64), primary_key=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("risk_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("decision_id", sa.String(64), nullable=True, unique=True),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("ordered_reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["testnet_risk_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes("proposal_id", "generation_id", "risk_input_digest"),
            name="ck_testnet_risk_materialization_hashes",
        ),
        sa.CheckConstraint(
            "verdict IN ('ALLOWED','DENIED','ERROR') AND "
            "((verdict='ERROR' AND decision_id IS NULL) OR "
            "(verdict IN ('ALLOWED','DENIED') AND decision_id IS NOT NULL))",
            name="ck_testnet_risk_materialization_terminal",
        ),
    )
    _append_only("testnet_risk_materialization_results")
    op.create_table(
        "testnet_approvals",
        sa.Column("approval_id", sa.String(64), primary_key=True),
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("decision_id", sa.String(64), nullable=False, unique=True),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("preview_digest", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("validity", sa.String(16), nullable=False),
        sa.Column("approval_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("approval_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("authority_binding_digest", sa.String(64), nullable=False),
        sa.Column("authority_binding", postgresql.JSONB(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["testnet_risk_decisions.decision_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["preview_digest"], ["testnet_order_previews.preview_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"], ["session_csrf_tokens.csrf_token_digest"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "approval_id",
                "proposal_id",
                "proposal_hash",
                "decision_id",
                "decision_hash",
                "preview_digest",
                "generation_id",
                "session_digest",
                "csrf_token_digest",
                "origin_hash",
                "approval_input_digest",
                "authority_binding_digest",
                "payload_hash",
            ),
            name="ck_testnet_approval_hashes",
        ),
        sa.CheckConstraint(
            "actor_id='operator-local-1' AND decision IN ('APPROVED','REJECTED') "
            "AND validity IN ('ACTIVE','EXPIRED','REVOKED','INVALIDATED')",
            name="ck_testnet_approval_actor_state",
        ),
        sa.CheckConstraint(
            "expires_at>approved_at AND expires_at<=approved_at + interval '5 minutes'",
            name="ck_testnet_approval_ttl",
        ),
    )
    _append_only("testnet_approvals")
    op.create_table(
        "testnet_approval_revocations",
        sa.Column("revocation_id", sa.String(64), primary_key=True),
        sa.Column("approval_id", sa.String(64), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["testnet_approvals.approval_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"], ["session_csrf_tokens.csrf_token_digest"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "revocation_id",
                "approval_id",
                "session_digest",
                "csrf_token_digest",
                "origin_hash",
                "payload_hash",
            ),
            name="ck_testnet_revocation_hashes",
        ),
    )
    _append_only("testnet_approval_revocations")
    op.create_table(
        "testnet_execution_authorizations",
        sa.Column("authorization_id", sa.String(64), primary_key=True),
        sa.Column("approval_id", sa.String(64), nullable=False, unique=True),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("authorization_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("authorization_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("authority_binding_digest", sa.String(64), nullable=False),
        sa.Column("authority_binding", postgresql.JSONB(), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("preview_digest", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["testnet_approvals.approval_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "authorization_id",
                "approval_id",
                "approval_hash",
                "authorization_input_digest",
                "authority_binding_digest",
                "proposal_hash",
                "decision_hash",
                "preview_digest",
                "generation_id",
            ),
            name="ck_testnet_authorization_hashes",
        ),
        sa.CheckConstraint(CLIENT_ID_CHECK, name="ck_testnet_authorization_client_id"),
        sa.CheckConstraint(
            "expires_at>issued_at AND expires_at<=issued_at + interval '5 minutes'",
            name="ck_testnet_authorization_ttl",
        ),
    )

    op.create_table(
        "testnet_cancel_authorizations",
        sa.Column("cancel_authorization_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("order_version", sa.BigInteger(), nullable=False),
        sa.Column("order_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("csrf_token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("origin_hash", sa.String(64), nullable=False),
        sa.Column("cancel_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("authorization_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("authority_binding", postgresql.JSONB(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["local_operators.actor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_digest"], ["operator_sessions.session_digest"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["csrf_token_digest"], ["session_csrf_tokens.csrf_token_digest"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "cancel_authorization_id",
                "order_id",
                "generation_id",
                "order_digest",
                "session_digest",
                "csrf_token_digest",
                "origin_hash",
                "authorization_input_digest",
            ),
            name="ck_testnet_cancel_authorization_hashes",
        ),
        sa.CheckConstraint(CLIENT_ID_CHECK, name="ck_testnet_cancel_authorization_client_id"),
        sa.CheckConstraint(
            "order_version>=1 AND expires_at>issued_at "
            "AND expires_at<=issued_at + interval '5 minutes' AND consumed_at>=issued_at",
            name="ck_testnet_cancel_authorization_ttl",
        ),
    )
    _append_only("testnet_cancel_authorizations")

    op.create_table(
        "testnet_gateway_commands",
        sa.Column("command_id", sa.String(64), primary_key=True),
        sa.Column("authorization_id", sa.String(64), nullable=True),
        sa.Column("cancel_authorization_id", sa.String(64), nullable=True),
        sa.Column("approval_id", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("command_type", sa.String(32), nullable=False),
        sa.Column("effect_class", sa.String(32), nullable=False),
        sa.Column("capability_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("command", postgresql.JSONB(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorization_id"],
            ["testnet_execution_authorizations.authorization_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancel_authorization_id"],
            ["testnet_cancel_authorizations.cancel_authorization_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["testnet_approvals.approval_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "command_id",
                "approval_id",
                "generation_id",
                "request_digest",
            ),
            name="ck_testnet_command_hashes",
        ),
        sa.CheckConstraint(CLIENT_ID_CHECK, name="ck_testnet_command_client_id"),
        sa.CheckConstraint(
            "(command_type='CANCEL_EXISTING_ORDER' AND authorization_id IS NULL "
            "AND cancel_authorization_id IS NOT NULL) OR "
            "(command_type<>'CANCEL_EXISTING_ORDER' AND authorization_id IS NOT NULL "
            "AND cancel_authorization_id IS NULL)",
            name="ck_testnet_command_authorization_kind",
        ),
        sa.CheckConstraint(
            "command_type IN ('SUBMIT_LIMIT_ORDER','CANCEL_EXISTING_ORDER',"
            "'QUERY_EXISTING_ORDER','RECONCILIATION_OBSERVATION')",
            name="ck_testnet_command_type",
        ),
        sa.CheckConstraint(
            "effect_class IN ('CREATE_ORDER','REDUCE_OR_CANCEL','OBSERVE_ONLY')",
            name="ck_testnet_effect_class",
        ),
        sa.CheckConstraint(
            "(command_type='SUBMIT_LIMIT_ORDER' AND effect_class='CREATE_ORDER' "
            "AND capability_id='SPOT_TESTNET_SUBMIT_LIMIT_GTC') OR "
            "(command_type='CANCEL_EXISTING_ORDER' AND effect_class='REDUCE_OR_CANCEL' "
            "AND capability_id='SPOT_TESTNET_CANCEL_BY_CLIENT_ID') OR "
            "(command_type='QUERY_EXISTING_ORDER' AND effect_class='OBSERVE_ONLY' "
            "AND capability_id='SPOT_TESTNET_QUERY_BY_CLIENT_ID') OR "
            "(command_type='RECONCILIATION_OBSERVATION' AND effect_class='OBSERVE_ONLY' "
            "AND capability_id IN ('SPOT_TESTNET_ACCOUNT','SPOT_TESTNET_OPEN_ORDERS_BY_SYMBOL',"
            "'SPOT_TESTNET_MY_TRADES_BY_ORDER'))",
            name="ck_testnet_command_capability",
        ),
    )
    op.create_index(
        "uq_testnet_create_command_client",
        "testnet_gateway_commands",
        ["generation_id", "client_order_id"],
        unique=True,
        postgresql_where=sa.text("effect_class='CREATE_ORDER'"),
    )
    _append_only("testnet_gateway_commands")
    op.create_table(
        "testnet_gateway_inbox",
        sa.Column("command_id", sa.String(64), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"], ["testnet_gateway_commands.command_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes("command_id", "request_digest"), name="ck_testnet_gateway_inbox_hashes"
        ),
    )
    _append_only("testnet_gateway_inbox")
    op.create_table(
        "testnet_gateway_dispatch_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column("command_id", sa.String(64), nullable=False, unique=True),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"], ["testnet_gateway_commands.command_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(_hashes("attempt_id", "command_id"), name="ck_testnet_dispatch_hashes"),
        sa.CheckConstraint(CLIENT_ID_CHECK, name="ck_testnet_dispatch_client_id"),
        sa.CheckConstraint(
            "status IN ('DISPATCH_RECORDED','SUBMISSION_UNKNOWN','COMPLETED')",
            name="ck_testnet_dispatch_status",
        ),
    )
    op.create_table(
        "testnet_gateway_receipts",
        sa.Column("receipt_id", sa.String(64), primary_key=True),
        sa.Column("command_id", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("external_effect_count", sa.SmallInteger(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"], ["testnet_gateway_commands.command_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("command_id", "status", name="uq_testnet_gateway_receipt_status"),
        sa.CheckConstraint(
            _hashes("receipt_id", "command_id", "request_digest"),
            name="ck_testnet_receipt_hashes",
        ),
        sa.CheckConstraint(
            "status IN ('ACCEPTED','REJECTED_LOCAL','DISPATCH_RECORDED',"
            "'SUBMISSION_UNKNOWN','EXCHANGE_ACKNOWLEDGED','RESOLUTION_REQUIRED',"
            "'RESOLVED_FOUND','RESOLVED_REJECTED','RESOLVED_NOT_FOUND_CONFIRMED')",
            name="ck_testnet_receipt_status",
        ),
        sa.CheckConstraint(
            "external_effect_count BETWEEN 0 AND 1",
            name="ck_testnet_receipt_effect_count",
        ),
    )
    _append_only("testnet_gateway_receipts")
    op.create_table(
        "testnet_gateway_observations",
        sa.Column("observation_id", sa.String(64), primary_key=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("semantic_key", sa.String(256), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_channel", sa.String(32), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes("observation_id", "generation_id", "payload_hash"),
            name="ck_testnet_observation_hashes",
        ),
        sa.CheckConstraint(
            "source_channel IN ('REST','USER_DATA')",
            name="ck_testnet_observation_channel",
        ),
    )
    _append_only("testnet_gateway_observations")
    op.create_table(
        "testnet_observation_inbox",
        sa.Column("observation_id", sa.String(64), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["testnet_gateway_observations.observation_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            _hashes("observation_id", "payload_hash"),
            name="ck_testnet_observation_inbox_hashes",
        ),
    )
    _append_only("testnet_observation_inbox")

    op.create_table(
        "testnet_asset_balances",
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("asset", sa.String(16), nullable=False),
        sa.Column("free", sa.Numeric(38, 18), nullable=False),
        sa.Column("locked", sa.Numeric(38, 18), nullable=False),
        sa.Column("source_observation_id", sa.String(64), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["testnet_account_generations.generation_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_observation_id"],
            ["testnet_gateway_observations.observation_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("generation_id", "asset"),
        sa.CheckConstraint(
            "asset ~ '^[A-Z0-9]{2,16}$' AND free>=0 AND locked>=0 AND version>=1",
            name="ck_testnet_asset_balance_values",
        ),
    )

    op.create_table(
        "testnet_orders",
        sa.Column("order_id", sa.String(64), primary_key=True),
        sa.Column("command_id", sa.String(64), nullable=False, unique=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("exchange_order_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("limit_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("external_outcome", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"], ["testnet_gateway_commands.command_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "generation_id", "client_order_id", name="uq_testnet_order_generation_client"
        ),
        sa.CheckConstraint(
            _hashes("order_id", "command_id", "generation_id"), name="ck_testnet_order_hashes"
        ),
        sa.CheckConstraint(CLIENT_ID_CHECK, name="ck_testnet_order_client_id"),
        sa.CheckConstraint(
            "quantity>0 AND limit_price>0 AND filled_quantity>=0 "
            "AND filled_quantity<=quantity AND version>=1",
            name="ck_testnet_order_amounts",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_SUBMIT','NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED')",
            name="ck_testnet_order_status",
        ),
        sa.CheckConstraint(
            "external_outcome IN ('QUEUED','DISPATCHED','UNKNOWN_OUTCOME','RECONCILING',"
            "'FOUND','REJECTED','NOT_FOUND_CONFIRMED','BLOCKED')",
            name="ck_testnet_order_outcome",
        ),
    )
    op.create_table(
        "testnet_fills",
        sa.Column("fill_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("external_trade_id", sa.String(128), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee_asset", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["testnet_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "generation_id", "external_trade_id", name="uq_testnet_fill_external_identity"
        ),
        sa.CheckConstraint(
            _hashes("fill_id", "order_id", "generation_id"), name="ck_testnet_fill_hashes"
        ),
        sa.CheckConstraint(
            "quantity>0 AND price>0 AND fee_amount>=0",
            name="ck_testnet_fill_amounts",
        ),
    )
    _append_only("testnet_fills")
    op.create_table(
        "testnet_order_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("observation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("order_version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["testnet_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["testnet_gateway_observations.observation_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("order_id", "order_version", name="uq_testnet_order_event_version"),
        sa.CheckConstraint(
            _hashes("event_id", "order_id", "observation_id", "payload_hash"),
            name="ck_testnet_order_event_hashes",
        ),
        sa.CheckConstraint(
            "event_type IN ('ORDER_OBSERVED','FILL_OBSERVED','CANCEL_OBSERVED',"
            "'UNKNOWN_RESOLVED','INVARIANT_REJECTED') AND order_version>=1",
            name="ck_testnet_order_event_type",
        ),
    )
    _append_only("testnet_order_events")
    op.create_table(
        "testnet_ledger_transactions",
        sa.Column("transaction_id", sa.String(64), primary_key=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("business_event_id", sa.String(64), nullable=False),
        sa.Column("journal_kind", sa.String(32), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversal_of", sa.String(64), nullable=True),
        sa.Column("replacement_for", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of"], ["testnet_ledger_transactions.transaction_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replacement_for"], ["testnet_ledger_transactions.transaction_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "generation_id",
            "business_event_id",
            "journal_kind",
            name="uq_testnet_ledger_business_journal",
        ),
        sa.CheckConstraint(
            _hashes("transaction_id", "generation_id", "business_event_id"),
            name="ck_testnet_ledger_transaction_hashes",
        ),
    )
    _append_only("testnet_ledger_transactions")
    op.create_table(
        "testnet_ledger_entries",
        sa.Column("transaction_id", sa.String(64), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("ledger_account", sa.String(128), nullable=False),
        sa.Column("commodity", sa.String(16), nullable=False),
        sa.Column("debit", sa.Numeric(38, 18), nullable=False),
        sa.Column("credit", sa.Numeric(38, 18), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["testnet_ledger_transactions.transaction_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("transaction_id", "line_no"),
        sa.CheckConstraint(
            "line_no>=1 AND debit>=0 AND credit>=0 AND ((debit>0) <> (credit>0))",
            name="ck_testnet_ledger_entry_amount",
        ),
    )
    _append_only("testnet_ledger_entries")
    op.execute("""
        CREATE FUNCTION enforce_testnet_ledger_balance() RETURNS trigger AS $$
        DECLARE target_id varchar(64);
        BEGIN
          target_id := COALESCE(NEW.transaction_id, OLD.transaction_id);
          IF (SELECT count(*) FROM testnet_ledger_entries
              WHERE transaction_id=target_id) < 2
             OR EXISTS (
               SELECT 1 FROM testnet_ledger_entries
               WHERE transaction_id=target_id
               GROUP BY commodity
               HAVING sum(debit)<>sum(credit)
             )
          THEN
            RAISE EXCEPTION 'Testnet ledger transaction must balance per commodity'
              USING ERRCODE='23514';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER testnet_ledger_transaction_balance
        AFTER INSERT ON testnet_ledger_transactions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_testnet_ledger_balance()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER testnet_ledger_entry_balance
        AFTER INSERT ON testnet_ledger_entries
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_testnet_ledger_balance()
    """)
    op.create_table(
        "testnet_reconciliation_checkpoints",
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("checkpoint_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("observation_set_digest", sa.String(64), nullable=False),
        sa.Column("ledger_snapshot_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("mismatch_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("operator_confirmation_digest", sa.String(64), nullable=True),
        sa.Column("watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["testnet_account_generations.generation_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            _hashes(
                "checkpoint_id",
                "generation_id",
                "checkpoint_digest",
                "observation_set_digest",
                "ledger_snapshot_digest",
            ),
            name="ck_testnet_reconciliation_hashes",
        ),
        sa.CheckConstraint(
            "status IN ('HEALTHY','RUNNING','FAILED','RESET_SUSPECTED',"
            "'AWAITING_OPERATOR_CONFIRMATION','UNKNOWN') AND version>=1",
            name="ck_testnet_reconciliation_status",
        ),
        sa.CheckConstraint(
            "mismatch_codes <@ ARRAY['ORDER_MISMATCH','KNOWN_ORDER_LOSS','FILL_MISMATCH',"
            "'TRADE_HISTORY_GAP','STALE_SNAPSHOT','SNAPSHOT_CYCLE_MISMATCH',"
            "'BALANCE_DISCONTINUITY','LEDGER_BALANCE_MISMATCH','FEE_MISMATCH',"
            "'BALANCE_MISMATCH','LEDGER_MISMATCH','AUTHORIZATION_RECEIPT_MISMATCH',"
            "'USER_DATA_GAP','ACCOUNT_GENERATION_MISMATCH','RESET_SIGNAL']::varchar[]",
            name="ck_testnet_reconciliation_reasons",
        ),
    )
    _append_only("testnet_reconciliation_checkpoints")

    op.execute("""
        CREATE VIEW testnet_pending_gateway_commands_v1 AS
        SELECT command_row.command_id,command_row.authorization_id,
               command_row.cancel_authorization_id,command_row.approval_id,
               command_row.generation_id,command_row.client_order_id,
               command_row.command_type,command_row.effect_class,command_row.capability_id,
               command_row.idempotency_key,command_row.request_digest,
               command_row.command,command_row.issued_at,command_row.expires_at,
               cancel.order_digest AS cancel_order_digest,
               cancel.authorization_input_digest AS cancel_authorization_input_digest,
               cancel.order_version AS cancel_order_version,
               cancel.client_order_id AS cancel_client_order_id,
               cancel.generation_id AS cancel_generation_id,
               cancel.issued_at AS cancel_issued_at,
               cancel.expires_at AS cancel_expires_at,
               cancel.consumed_at AS cancel_consumed_at
        FROM testnet_gateway_commands command_row
        LEFT JOIN testnet_cancel_authorizations cancel USING(cancel_authorization_id)
        WHERE command_row.expires_at>CURRENT_TIMESTAMP
          AND NOT EXISTS (
            SELECT 1 FROM testnet_gateway_inbox inbox
            WHERE inbox.command_id=command_row.command_id)
    """)
    op.execute("""
        CREATE VIEW testnet_operator_state_v1 AS
        SELECT account.account_binding_id,account.environment,account.account_label,
               generation.generation_id,generation.generation_number,
               generation.status AS generation_status,generation.version AS generation_version,
               safety.active AS testnet_barrier_active,safety.version AS barrier_version,
               safety.reason_code,
               (SELECT active FROM kill_switch_state WHERE scope='paper-global')
                 AS paper_kill_active,
               (SELECT version FROM kill_switch_state WHERE scope='paper-global')
                 AS paper_kill_version,
               activation.activation_id,
               CASE WHEN activation_revocation.activation_id IS NOT NULL THEN 'REVOKED'
                    ELSE activation.status END AS activation_status,
               activation.version AS activation_version,activation.expires_at AS activation_expires_at,
               activation.gateway_instance_id,activation.build_digest,
               activation.configuration_digest,activation.allowlist_digest,
               checkpoint.checkpoint_id,checkpoint.checkpoint_digest,
                checkpoint.status AS reconciliation_status,checkpoint.version AS checkpoint_version,
                checkpoint.created_at AS checkpoint_created_at,
                checkpoint.watermark_at AS checkpoint_watermark_at,
                (SELECT count(*) FROM testnet_ledger_transactions ledger_tx
                 WHERE ledger_tx.generation_id=generation.generation_id) AS ledger_version
        FROM testnet_safety_state safety
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_accounts ORDER BY created_at DESC LIMIT 1
        ) account ON true
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_account_generations generation_row
          WHERE generation_row.account_binding_id=account.account_binding_id
          ORDER BY generation_number DESC LIMIT 1
        ) generation ON true
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_activations activation_row
          WHERE activation_row.generation_id=generation.generation_id
          ORDER BY activated_at DESC LIMIT 1
        ) activation ON true
        LEFT JOIN testnet_activation_revocations activation_revocation
          ON activation_revocation.activation_id=activation.activation_id
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_reconciliation_checkpoints checkpoint_row
          WHERE checkpoint_row.generation_id=generation.generation_id
          ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1
        ) checkpoint ON true
        WHERE safety.scope='testnet-global'
    """)
    op.execute("""
        CREATE VIEW testnet_approval_view_v1 AS
        SELECT preview.proposal_id,preview.proposal_hash,preview.preview_digest,
               preview.preview,preview.created_at AS preview_created_at,
               preview.expires_at AS preview_expires_at,
               decision.decision_id,decision.decision_hash,decision.risk_input_digest,
               decision.risk_input,
               decision.verdict,decision.policy_version,decision.ordered_reason_codes,
               decision.decided_at AS decision_decided_at,
               decision.expires_at AS decision_expires_at,
               account.account_binding_id,account.environment,
               generation.generation_number,generation.version AS generation_version,
               approval.approval_id,approval.decision AS approval_decision,
               CASE WHEN approval_revocation.approval_id IS NOT NULL THEN 'REVOKED'
                    ELSE approval.validity END AS approval_validity,
               approval_revocation.revoked_at AS approval_revoked_at,
               auth.authorization_id,auth.consumed_at,
               gateway_command.command_id
        FROM testnet_order_previews preview
        JOIN testnet_risk_decisions decision ON decision.preview_digest=preview.preview_digest
        JOIN testnet_account_generations generation ON generation.generation_id=preview.generation_id
        JOIN testnet_accounts account USING(account_binding_id)
        LEFT JOIN testnet_approvals approval ON approval.decision_id=decision.decision_id
        LEFT JOIN testnet_approval_revocations approval_revocation
          ON approval_revocation.approval_id=approval.approval_id
        LEFT JOIN testnet_execution_authorizations auth
          ON auth.approval_id=approval.approval_id
        LEFT JOIN testnet_gateway_commands gateway_command
          ON gateway_command.authorization_id=auth.authorization_id
    """)
    op.execute("""
        CREATE VIEW testnet_cancel_view_v1 AS
        SELECT order_row.order_id,order_row.command_id,order_row.generation_id,
               account.environment,generation.generation_number,
               order_row.client_order_id,order_row.exchange_order_id,
               order_row.symbol,order_row.side,order_row.quantity,order_row.limit_price,
               order_row.filled_quantity,order_row.status,order_row.external_outcome,
               order_row.version,
               cancel.cancel_authorization_id,cancel.order_digest,
               cancel.authorization_input_digest AS cancel_authorization_input_digest,
               cancel.issued_at AS cancel_issued_at,cancel.expires_at AS cancel_expires_at,
               cancel.consumed_at AS cancel_consumed_at,
               cancel_command.command_id AS cancel_command_id
        FROM testnet_orders order_row
        JOIN testnet_account_generations generation USING(generation_id)
        JOIN testnet_accounts account USING(account_binding_id)
        LEFT JOIN testnet_cancel_authorizations cancel USING(order_id)
        LEFT JOIN testnet_gateway_commands cancel_command
          ON cancel_command.cancel_authorization_id=cancel.cancel_authorization_id
    """)
    op.execute("""
        CREATE VIEW testnet_execution_view_v1 AS
        SELECT gateway_command.command_id AS execution_id,gateway_command.authorization_id,
               gateway_command.cancel_authorization_id,
               gateway_command.command_id,gateway_command.client_order_id,
               account.environment,generation.generation_number,
               COALESCE(receipt.status,'QUEUED') AS command_status,
               COALESCE(order_row.external_outcome,'QUEUED') AS external_outcome,
               order_row.order_id,order_row.exchange_order_id,order_row.symbol,order_row.side,
               order_row.quantity,order_row.limit_price,order_row.filled_quantity,
               order_row.status AS order_status,order_row.version AS order_version,
               checkpoint.status AS reconciliation_status,
               checkpoint.checkpoint_id,generation.status AS reset_state,
               gateway_command.issued_at AS submitted_at,
               observation.received_at AS last_observed_at
        FROM testnet_gateway_commands gateway_command
        JOIN testnet_account_generations generation USING(generation_id)
        JOIN testnet_accounts account USING(account_binding_id)
        LEFT JOIN testnet_orders order_row USING(command_id)
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_gateway_receipts receipt_row
          WHERE receipt_row.command_id=gateway_command.command_id
          ORDER BY observed_at DESC LIMIT 1
        ) receipt ON true
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_reconciliation_checkpoints checkpoint_row
          WHERE checkpoint_row.generation_id=generation.generation_id
          ORDER BY created_at DESC LIMIT 1
        ) checkpoint ON true
        LEFT JOIN LATERAL (
          SELECT * FROM testnet_gateway_observations observation_row
          WHERE observation_row.generation_id=generation.generation_id
            AND observation_row.client_order_id=gateway_command.client_order_id
          ORDER BY received_at DESC LIMIT 1
        ) observation ON true
    """)
    op.execute("""
        CREATE VIEW testnet_operator_intent_receipts_v1 AS
        SELECT idempotency_key,request_digest,receipt FROM testnet_operator_intents
    """)
    op.execute("""
        CREATE VIEW testnet_gateway_authority_v1 AS
        SELECT account_binding_id,environment,generation_id,generation_number,
               generation_status,testnet_barrier_active,barrier_version,paper_kill_active,
                activation_id,activation_status,activation_version,activation_expires_at,
                gateway_instance_id,build_digest,configuration_digest,allowlist_digest,
                reconciliation_status
        FROM testnet_operator_state_v1
    """)
    op.execute("""
        CREATE VIEW testnet_gateway_receipt_reader_v1 AS
        SELECT receipt_id,command_id,request_digest,status,external_effect_count,observed_at
        FROM testnet_gateway_receipts
    """)
    op.execute("""
        CREATE VIEW testnet_gateway_unresolved_dispatch_v1 AS
        SELECT attempt.attempt_id,attempt.command_id,attempt.client_order_id,
               command.request_digest,attempt.started_at
        FROM testnet_gateway_dispatch_attempts attempt
        JOIN testnet_gateway_commands command USING(command_id)
        WHERE NOT EXISTS (
          SELECT 1 FROM testnet_gateway_receipts receipt
          WHERE receipt.command_id=attempt.command_id
            AND receipt.status IN ('SUBMISSION_UNKNOWN','EXCHANGE_ACKNOWLEDGED',
              'RESOLUTION_REQUIRED','RESOLVED_FOUND','RESOLVED_REJECTED',
              'RESOLVED_NOT_FOUND_CONFIRMED','REJECTED_LOCAL'))
    """)

    op.execute(
        "GRANT SELECT ON trade_proposals, risk_decisions, kill_switch_state,"
        "operator_sessions,session_csrf_tokens "
        "TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT SELECT, INSERT ON testnet_accounts, testnet_account_generations, "
        "testnet_activations, testnet_activation_revocations, testnet_operator_intents, "
        "testnet_operator_intent_results, testnet_domain_events, testnet_outbox, "
        "testnet_order_previews, testnet_risk_decisions, testnet_risk_materialization_results, "
        "testnet_approvals, testnet_approval_revocations, testnet_execution_authorizations, "
        "testnet_cancel_authorizations, "
        "testnet_gateway_commands, testnet_observation_inbox, testnet_asset_balances, "
        "testnet_orders, testnet_order_events, testnet_fills, "
        "testnet_ledger_transactions, testnet_ledger_entries, "
        "testnet_reconciliation_checkpoints TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT SELECT, UPDATE (active,version,last_event_id,reason_code,updated_at) "
        "ON testnet_safety_state TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT UPDATE (status,opening_snapshot_digest,closed_at,version) "
        "ON testnet_account_generations "
        "TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT UPDATE (free,locked,source_observation_id,version,updated_at) "
        "ON testnet_asset_balances TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT UPDATE (exchange_order_id,filled_quantity,status,external_outcome,version) "
        "ON testnet_orders TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT SELECT ON testnet_gateway_receipts, testnet_gateway_observations "
        "TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT SELECT ON testnet_operator_state_v1,testnet_approval_view_v1 "
        "TO woozoo_testnet_execution"
    )
    op.execute(
        "GRANT SELECT ON testnet_pending_gateway_commands_v1,"
        "testnet_gateway_authority_v1,testnet_gateway_receipt_reader_v1,"
        "testnet_gateway_unresolved_dispatch_v1 TO woozoo_spot_testnet_gateway"
    )
    op.execute(
        "GRANT INSERT ON testnet_gateway_inbox, testnet_gateway_dispatch_attempts, "
        "testnet_gateway_receipts, testnet_gateway_observations TO woozoo_spot_testnet_gateway"
    )
    op.execute("GRANT INSERT ON testnet_operator_intents TO woozoo_control_api")
    op.execute(
        "GRANT SELECT ON testnet_operator_state_v1,testnet_approval_view_v1,"
        "testnet_execution_view_v1,testnet_cancel_view_v1,"
        "testnet_operator_intent_receipts_v1 "
        "TO woozoo_control_api"
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM testnet_gateway_commands)
             OR EXISTS (SELECT 1 FROM testnet_gateway_dispatch_attempts)
             OR EXISTS (SELECT 1 FROM testnet_gateway_observations)
             OR EXISTS (SELECT 1 FROM testnet_orders)
             OR EXISTS (SELECT 1 FROM testnet_fills)
             OR EXISTS (SELECT 1 FROM testnet_ledger_transactions)
          THEN RAISE EXCEPTION 'Phase 8 downgrade blocked: immutable Testnet history exists';
          END IF;
        END $$
    """)
    op.execute("DROP VIEW testnet_gateway_unresolved_dispatch_v1")
    op.execute("DROP VIEW testnet_gateway_receipt_reader_v1")
    op.execute("DROP VIEW testnet_gateway_authority_v1")
    op.execute("DROP VIEW testnet_operator_intent_receipts_v1")
    op.execute("DROP VIEW testnet_execution_view_v1")
    op.execute("DROP VIEW testnet_cancel_view_v1")
    op.execute("DROP VIEW testnet_approval_view_v1")
    op.execute("DROP VIEW testnet_operator_state_v1")
    op.execute("DROP VIEW testnet_pending_gateway_commands_v1")
    for table in (
        "testnet_reconciliation_checkpoints",
        "testnet_ledger_entries",
        "testnet_ledger_transactions",
        "testnet_order_events",
        "testnet_fills",
        "testnet_orders",
        "testnet_asset_balances",
        "testnet_observation_inbox",
        "testnet_gateway_observations",
        "testnet_gateway_receipts",
        "testnet_gateway_dispatch_attempts",
        "testnet_gateway_inbox",
        "testnet_gateway_commands",
        "testnet_cancel_authorizations",
        "testnet_execution_authorizations",
        "testnet_approval_revocations",
        "testnet_approvals",
        "testnet_risk_materialization_results",
        "testnet_risk_decisions",
        "testnet_order_previews",
        "testnet_outbox",
        "testnet_domain_events",
        "testnet_operator_intent_results",
        "testnet_operator_intents",
        "testnet_safety_state",
        "testnet_activation_revocations",
        "testnet_activations",
        "testnet_account_generations",
        "testnet_accounts",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION enforce_testnet_ledger_balance()")
    op.execute(
        "REVOKE ALL PRIVILEGES ON trade_proposals,risk_decisions,kill_switch_state,"
        "operator_sessions,session_csrf_tokens "
        "FROM woozoo_testnet_execution"
    )
    op.execute(
        "REVOKE USAGE ON SCHEMA public FROM woozoo_testnet_execution, woozoo_spot_testnet_gateway"
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_spot_testnet_gateway') THEN
            DROP ROLE woozoo_spot_testnet_gateway;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_testnet_execution') THEN
            DROP ROLE woozoo_testnet_execution;
          END IF;
        END $$
    """)
