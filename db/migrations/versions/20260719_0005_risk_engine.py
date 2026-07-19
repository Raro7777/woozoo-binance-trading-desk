"""Create dormant Phase 5 Risk authority and monotonic Paper Kill barrier."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260719_0005"
down_revision = "20260719_0004"
branch_labels = None
depends_on = None


def _append_only(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_risk_history_mutation()
    """)


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_risk_history_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Phase 5 Risk/Kill history is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.create_table(
        "risk_policy_versions",
        sa.Column("policy_version", sa.String(64), primary_key=True),
        sa.Column("policy_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("policy_hash ~ '^[a-f0-9]{64}$'", name="ck_risk_policy_hash"),
    )
    op.create_table(
        "risk_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("risk_input_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("risk_input", postgresql.JSONB(), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("primary_reason", sa.String(64), nullable=False),
        sa.Column("ordered_reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("portfolio_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("data_state_hash", sa.String(64), nullable=False),
        sa.Column("paper_order_preview_hash", sa.String(64), nullable=False),
        sa.Column("reconciliation_checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("kill_switch_version", sa.BigInteger(), nullable=False),
        sa.Column("decision_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_version"], ["risk_policy_versions.policy_version"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("decision_id ~ '^[a-f0-9]{64}$'", name="ck_risk_decision_id"),
        sa.CheckConstraint(
            "risk_input_digest ~ '^[a-f0-9]{64}$' AND decision_hash ~ '^[a-f0-9]{64}$'",
            name="ck_risk_decision_hashes",
        ),
        sa.CheckConstraint("verdict IN ('ALLOWED','DENIED','ERROR')", name="ck_risk_verdict"),
        sa.CheckConstraint("kill_switch_version>=0", name="ck_risk_kill_version"),
    )
    op.create_table(
        "kill_switch_events",
        sa.Column("activation_event_id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_digest", sa.String(64), nullable=False),
        sa.Column("prior_version", sa.BigInteger(), nullable=False),
        sa.Column("new_version", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("scope", "new_version", name="uq_kill_scope_version"),
        sa.CheckConstraint("scope='paper-global'", name="ck_kill_event_scope"),
        sa.CheckConstraint("trigger_kind IN ('MANUAL','INVARIANT')", name="ck_kill_trigger"),
        sa.CheckConstraint(
            "reason_code IN ('MANUAL_SAFETY_STOP','LEDGER_IMBALANCE',"
            "'PHYSICAL_LEDGER_MISMATCH','AUTHORIZATION_RECEIPT_MISMATCH')",
            name="ck_kill_reason_code",
        ),
        sa.CheckConstraint("new_version=prior_version+1", name="ck_kill_version_step"),
        sa.CheckConstraint(
            "activation_event_id ~ '^[a-f0-9]{64}$' AND request_hash ~ '^[a-f0-9]{64}$' "
            "AND context_digest ~ '^[a-f0-9]{64}$'",
            name="ck_kill_hashes",
        ),
    )
    op.create_table(
        "kill_switch_state",
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("last_activation_event_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["last_activation_event_id"],
            ["kill_switch_events.activation_event_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("scope='paper-global'", name="ck_kill_state_scope"),
        sa.CheckConstraint(
            "(active=false AND version=0 AND last_activation_event_id IS NULL) OR "
            "(active=true AND version>0 AND last_activation_event_id IS NOT NULL)",
            name="ck_kill_state_monotonic",
        ),
    )
    op.create_table(
        "risk_kill_command_receipts",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("activation_event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["activation_event_id"],
            ["kill_switch_events.activation_event_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("request_hash ~ '^[a-f0-9]{64}$'", name="ck_kill_receipt_hash"),
    )
    op.create_table(
        "risk_outbox_links",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("aggregate_kind", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.event_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "aggregate_kind IN ('risk-decision','kill-switch')", name="ck_risk_outbox_kind"
        ),
    )
    op.create_table(
        "paper_kill_inbox",
        sa.Column("activation_event_id", sa.String(64), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["activation_event_id"],
            ["kill_switch_events.activation_event_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("payload_hash ~ '^[a-f0-9]{64}$'", name="ck_paper_kill_inbox_hash"),
    )
    op.create_table(
        "paper_kill_cancel_batches",
        sa.Column("activation_event_id", sa.String(64), primary_key=True),
        sa.Column("batch_key", sa.String(64), primary_key=True),
        sa.Column("paper_account_id", sa.String(64), nullable=False),
        sa.Column("first_cursor", postgresql.JSONB(), nullable=False),
        sa.Column("last_cursor", postgresql.JSONB(), nullable=False),
        sa.Column("cancelled_count", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["activation_event_id"],
            ["paper_kill_inbox.activation_event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("batch_key ~ '^[a-f0-9]{64}$'", name="ck_paper_kill_batch_hash"),
        sa.CheckConstraint("cancelled_count BETWEEN 1 AND 100", name="ck_paper_kill_batch_bound"),
    )
    op.create_table(
        "paper_kill_cancel_items",
        sa.Column("activation_event_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), primary_key=True),
        sa.Column("batch_key", sa.String(64), nullable=False),
        sa.Column("paper_account_id", sa.String(64), nullable=False),
        sa.Column("cancel_id", sa.String(128), nullable=False, unique=True),
        sa.Column("released_asset", sa.String(8), nullable=False),
        sa.Column("released_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["activation_event_id", "batch_key"],
            [
                "paper_kill_cancel_batches.activation_event_id",
                "paper_kill_cancel_batches.batch_key",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["paper_account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("released_amount>=0", name="ck_paper_kill_release_nonnegative"),
    )

    op.execute("""
        INSERT INTO risk_policy_versions(policy_version,policy_hash,created_at)
        VALUES ('woozoo.risk-policy/v1',
          'f031ed0eaceafefce69f63b5470cc8678ef2e2d28b2c80d8e5da02ee0ae7960d',
          '2026-07-19T00:00:00Z')
    """)
    op.execute("""
        INSERT INTO kill_switch_state(scope,active,version,last_activation_event_id)
        VALUES ('paper-global',false,0,NULL)
    """)
    op.execute("""
        CREATE FUNCTION enforce_kill_state_activation_only() RETURNS trigger AS $$
        BEGIN
          IF OLD.scope<>'paper-global' OR OLD.active OR OLD.version<>0
             OR NEW.scope<>OLD.scope OR NEW.active IS DISTINCT FROM true
             OR NEW.version<>OLD.version+1 OR NEW.last_activation_event_id IS NULL
             OR NOT EXISTS (
               SELECT 1 FROM kill_switch_events event
               WHERE event.activation_event_id=NEW.last_activation_event_id
                 AND event.scope=NEW.scope
                 AND event.prior_version=OLD.version
                 AND event.new_version=NEW.version)
          THEN
            RAISE EXCEPTION 'Kill state permits monotonic activation only';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION enforce_kill_state_activation_only() FROM PUBLIC")
    op.execute("""
        CREATE TRIGGER kill_switch_state_activation_only
        BEFORE UPDATE ON kill_switch_state FOR EACH ROW
        EXECUTE FUNCTION enforce_kill_state_activation_only()
    """)
    op.execute("""
        CREATE FUNCTION assert_kill_activation_consistency() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM kill_switch_events event
            LEFT JOIN risk_kill_command_receipts receipt
              ON receipt.request_id=event.request_id
             AND receipt.request_hash=event.request_hash
             AND receipt.activation_event_id=event.activation_event_id
            LEFT JOIN risk_outbox_links link
              ON link.aggregate_kind='kill-switch'
             AND link.aggregate_id=event.activation_event_id
            LEFT JOIN outbox_events outbox ON outbox.event_id=link.event_id
            WHERE receipt.request_id IS NULL OR link.event_id IS NULL OR outbox.event_id IS NULL
               OR outbox.event_type<>'kill-switch.activated.v1'
               OR outbox.aggregate_type<>'kill_switch'
               OR outbox.aggregate_id<>event.activation_event_id
               OR outbox.aggregate_version<>event.new_version
               OR outbox.payload->>'producer'<>'risk-engine'
               OR outbox.payload->'data'->>'activation_event_id'<>event.activation_event_id
          ) OR EXISTS (
            SELECT 1 FROM kill_switch_state state
            LEFT JOIN kill_switch_events event
              ON event.activation_event_id=state.last_activation_event_id
             AND event.scope=state.scope AND event.new_version=state.version
            WHERE state.active AND event.activation_event_id IS NULL
          ) OR EXISTS (
            SELECT 1 FROM risk_kill_command_receipts receipt
            LEFT JOIN kill_switch_events event
              ON event.activation_event_id=receipt.activation_event_id
             AND event.request_id=receipt.request_id
             AND event.request_hash=receipt.request_hash
            WHERE event.activation_event_id IS NULL
          ) OR EXISTS (
            SELECT 1 FROM risk_outbox_links link
            LEFT JOIN kill_switch_events event
              ON event.activation_event_id=link.aggregate_id
            LEFT JOIN outbox_events outbox ON outbox.event_id=link.event_id
            WHERE link.aggregate_kind='kill-switch'
              AND (event.activation_event_id IS NULL OR outbox.event_id IS NULL)
          ) THEN
            RAISE EXCEPTION 'Kill activation transaction is incomplete';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION assert_kill_activation_consistency() FROM PUBLIC")
    for table in (
        "kill_switch_events",
        "kill_switch_state",
        "risk_kill_command_receipts",
        "risk_outbox_links",
    ):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {table}_kill_activation_consistency
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION assert_kill_activation_consistency()
        """)
    op.execute("""
        CREATE FUNCTION paper_lock_kill_barrier()
        RETURNS TABLE(active boolean, version bigint, last_activation_event_id varchar) AS $$
          SELECT state.active,state.version,state.last_activation_event_id
          FROM kill_switch_state state WHERE state.scope='paper-global' FOR SHARE
        $$ LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute("REVOKE ALL ON FUNCTION paper_lock_kill_barrier() FROM PUBLIC")

    for table in (
        "risk_policy_versions",
        "risk_decisions",
        "kill_switch_events",
        "risk_kill_command_receipts",
        "risk_outbox_links",
        "paper_kill_inbox",
        "paper_kill_cancel_batches",
        "paper_kill_cancel_items",
    ):
        _append_only(table)

    # Preserve every Phase 4 relational check, changing only the assumption that
    # all cancelled orders have a human-command receipt. A system Kill item is a
    # separate immutable cancellation authority.
    op.execute(r"""
        DO $$
        DECLARE
          v_definition text;
          v_replaced text;
        BEGIN
          SELECT pg_get_functiondef('assert_paper_relational_consistency()'::regprocedure)
            INTO v_definition;
          v_replaced := regexp_replace(
            v_definition,
            'cancelled_receipts\.receipt_count\s*<>\s*1',
            '(cancelled_receipts.receipt_count<>1 AND NOT EXISTS (SELECT 1 FROM '
            'paper_kill_cancel_items kill_item WHERE kill_item.order_id=paper_order.order_id))',
            'g'
          );
          IF v_replaced=v_definition THEN
            RAISE EXCEPTION 'Could not extend Phase 4 cancellation consistency';
          END IF;
          EXECUTE v_replaced;
        END $$
    """)
    op.execute(r"""
        DO $$
        DECLARE
          v_definition text;
          v_replaced text;
        BEGIN
          SELECT pg_get_functiondef('assert_paper_relational_consistency()'::regprocedure)
            INTO v_definition;
          v_replaced := regexp_replace(
            v_definition,
            $pattern$\(event\.event_type='paper\.order\.cancelled\.v1' AND \(\s*input\.source_kind IS DISTINCT FROM 'TEST_COMMAND'\s*OR NOT EXISTS \(\s*SELECT 1 FROM paper_command_receipts receipt\s*WHERE receipt\.paper_order_id=event\.order_id\s*AND receipt\.broker_seq=input\.broker_seq\s*AND receipt\.outcome='ORDER_CANCELLED'\)\)\)$pattern$,
            $replacement$(event.event_type='paper.order.cancelled.v1' AND ((input.source_kind IS DISTINCT FROM 'TEST_COMMAND' OR NOT EXISTS (SELECT 1 FROM paper_command_receipts receipt WHERE receipt.paper_order_id=event.order_id AND receipt.broker_seq=input.broker_seq AND receipt.outcome='ORDER_CANCELLED')) AND NOT EXISTS (SELECT 1 FROM paper_kill_cancel_items kill_item WHERE kill_item.order_id=event.order_id)))$replacement$,
            'g'
          );
          IF v_replaced=v_definition THEN
            RAISE EXCEPTION 'Could not extend Phase 4 Kill event authority';
          END IF;
          EXECUTE v_replaced;
        END $$
    """)
    op.execute(r"""
        DO $$
        DECLARE
          v_definition text;
          v_replaced text;
        BEGIN
          SELECT pg_get_functiondef(
            'append_paper_outbox(varchar,varchar,jsonb,varchar,timestamptz,varchar,varchar,bigint)'
              ::regprocedure
          ) INTO v_definition;
          v_replaced := regexp_replace(
            v_definition,
            'AND receipt\.outcome\s*=\s*''ORDER_CANCELLED''\)\)',
            'AND receipt.outcome=''ORDER_CANCELLED'') AND NOT EXISTS (SELECT 1 FROM '
            'paper_kill_cancel_items kill_item WHERE kill_item.order_id=p_aggregate_id '
            'AND kill_item.cancel_id=p_payload->''data''->>''cancel_id''))',
            'g'
          );
          IF v_replaced=v_definition THEN
            RAISE EXCEPTION 'Could not extend Phase 4 Kill outbox authority';
          END IF;
          EXECUTE v_replaced;
        END $$
    """)

    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_risk_engine') THEN
            CREATE ROLE woozoo_risk_engine LOGIN;
          END IF;
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_risk_engine")
    op.execute(
        "GRANT SELECT ON risk_policy_versions, risk_decisions, kill_switch_events, "
        "kill_switch_state, risk_kill_command_receipts, risk_outbox_links TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT INSERT ON risk_decisions, kill_switch_events, risk_kill_command_receipts, "
        "risk_outbox_links, outbox_events TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT UPDATE (active,version,last_activation_event_id) ON kill_switch_state "
        "TO woozoo_risk_engine"
    )
    op.execute(
        "GRANT SELECT ON kill_switch_state, kill_switch_events, risk_outbox_links "
        "TO woozoo_paper_engine"
    )
    op.execute("GRANT EXECUTE ON FUNCTION paper_lock_kill_barrier() TO woozoo_paper_engine")
    op.execute(
        "GRANT SELECT, INSERT ON paper_kill_inbox, paper_kill_cancel_batches, "
        "paper_kill_cancel_items TO woozoo_paper_engine"
    )


def downgrade() -> None:
    op.execute(
        "CREATE TEMP TABLE phase5_outbox_cleanup AS "
        "SELECT event_id FROM risk_outbox_links UNION "
        "SELECT event_id FROM outbox_events WHERE payload->>'producer'='risk-engine'"
    )
    op.execute(r"""
        DO $$
        DECLARE v_definition text; v_replaced text;
        BEGIN
          SELECT pg_get_functiondef(
            'append_paper_outbox(varchar,varchar,jsonb,varchar,timestamptz,varchar,varchar,bigint)'
              ::regprocedure
          ) INTO v_definition;
          v_replaced := regexp_replace(
            v_definition,
            'AND receipt\.outcome=''ORDER_CANCELLED''\) AND NOT EXISTS \(SELECT 1 FROM '
            'paper_kill_cancel_items kill_item WHERE kill_item\.order_id=p_aggregate_id '
            'AND kill_item\.cancel_id=p_payload->''data''->>''cancel_id''\)\)',
            'AND receipt.outcome=''ORDER_CANCELLED''))',
            'g'
          );
          EXECUTE v_replaced;
        END $$
    """)
    op.execute(r"""
        DO $$
        DECLARE v_definition text; v_replaced text;
        BEGIN
          SELECT pg_get_functiondef('assert_paper_relational_consistency()'::regprocedure)
            INTO v_definition;
          v_replaced := regexp_replace(
            v_definition,
            $pattern$\(event\.event_type='paper\.order\.cancelled\.v1' AND \(\(input\.source_kind IS DISTINCT FROM 'TEST_COMMAND' OR NOT EXISTS \(\s*SELECT 1 FROM paper_command_receipts receipt\s*WHERE receipt\.paper_order_id=event\.order_id\s*AND receipt\.broker_seq=input\.broker_seq\s*AND receipt\.outcome='ORDER_CANCELLED'\)\)\s*AND NOT EXISTS \(SELECT 1 FROM paper_kill_cancel_items kill_item\s*WHERE kill_item\.order_id=event\.order_id\)\)\)$pattern$,
            $replacement$(event.event_type='paper.order.cancelled.v1' AND (input.source_kind IS DISTINCT FROM 'TEST_COMMAND' OR NOT EXISTS (SELECT 1 FROM paper_command_receipts receipt WHERE receipt.paper_order_id=event.order_id AND receipt.broker_seq=input.broker_seq AND receipt.outcome='ORDER_CANCELLED')))$replacement$,
            'g'
          );
          EXECUTE v_replaced;
        END $$
    """)
    op.execute(r"""
        DO $$
        DECLARE v_definition text; v_replaced text;
        BEGIN
          SELECT pg_get_functiondef('assert_paper_relational_consistency()'::regprocedure)
            INTO v_definition;
          v_replaced := regexp_replace(
            v_definition,
            '\(cancelled_receipts\.receipt_count<>1 AND NOT EXISTS \(SELECT 1 FROM '
            'paper_kill_cancel_items kill_item WHERE kill_item\.order_id=paper_order\.order_id\)\)',
            'cancelled_receipts.receipt_count<>1',
            'g'
          );
          EXECUTE v_replaced;
        END $$
    """)
    op.execute("REVOKE ALL ON outbox_events FROM woozoo_risk_engine")
    op.execute("DROP FUNCTION IF EXISTS paper_lock_kill_barrier()")
    for table in (
        "paper_kill_cancel_items",
        "paper_kill_cancel_batches",
        "paper_kill_inbox",
        "risk_outbox_links",
        "risk_kill_command_receipts",
        "kill_switch_state",
        "kill_switch_events",
        "risk_decisions",
        "risk_policy_versions",
    ):
        op.drop_table(table)
    op.execute(
        "DELETE FROM outbox_events WHERE event_id IN (SELECT event_id FROM phase5_outbox_cleanup)"
    )
    op.execute("DROP TABLE phase5_outbox_cleanup")
    op.execute("DROP FUNCTION enforce_kill_state_activation_only()")
    op.execute("DROP FUNCTION assert_kill_activation_consistency()")
    # Alembic may continue directly into the Phase 4 downgrade in the same
    # transaction. Drain deferred outbox consistency triggers before that
    # migration alters the shared outbox table.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("DROP FUNCTION reject_risk_history_mutation()")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_risk_engine') THEN
            REVOKE USAGE ON SCHEMA public FROM woozoo_risk_engine;
            DROP ROLE woozoo_risk_engine;
          END IF;
        END $$
    """)
