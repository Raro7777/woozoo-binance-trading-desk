"""Create dormant Phase 4 Paper Broker and immutable ledger storage."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260719_0004"
down_revision = "20260719_0003"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(38, 18)


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("aggregate_type", sa.String(64), nullable=True))
    op.add_column("outbox_events", sa.Column("aggregate_id", sa.String(64), nullable=True))
    op.add_column("outbox_events", sa.Column("aggregate_version", sa.BigInteger(), nullable=True))
    op.create_index(
        "uq_paper_outbox_aggregate_version",
        "outbox_events",
        ["aggregate_type", "aggregate_id", "aggregate_version", "event_type"],
        unique=True,
        postgresql_where=sa.text("aggregate_type IS NOT NULL"),
    )
    op.create_table(
        "paper_policy_versions",
        sa.Column("policy_version", sa.String(64), primary_key=True),
        sa.Column("fee_rate", MONEY, nullable=False),
        sa.Column("participation_rate", MONEY, nullable=False),
        sa.Column("quantity_step", MONEY, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fee_rate=0.001", name="ck_paper_policy_fee_rate"),
        sa.CheckConstraint("participation_rate=0.10", name="ck_paper_policy_participation"),
        sa.CheckConstraint("quantity_step>0", name="ck_paper_policy_step"),
    )
    op.create_table(
        "paper_accounts",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("namespace", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("namespace='test'", name="ck_p4_paper_account_test_only"),
    )
    op.create_table(
        "paper_symbol_rule_versions",
        sa.Column("rule_version", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("tick_size", MONEY, nullable=False),
        sa.Column("step_size", MONEY, nullable=False),
        sa.Column("min_quantity", MONEY, nullable=False),
        sa.Column("min_notional", MONEY, nullable=False),
        sa.Column("source_payload_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_paper_rule_symbol"),
        sa.CheckConstraint(
            "tick_size>0 AND step_size>0 AND min_quantity>0 AND min_notional>0",
            name="ck_paper_rule_positive",
        ),
    )
    op.create_table(
        "paper_asset_balances",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("asset", sa.String(8), primary_key=True),
        sa.Column("available", MONEY, nullable=False),
        sa.Column("held", MONEY, nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("asset IN ('BTC','ETH','USDT')", name="ck_paper_balance_asset"),
        sa.CheckConstraint("available>=0 AND held>=0", name="ck_paper_balance_nonnegative"),
        sa.CheckConstraint("version>=0", name="ck_paper_balance_version"),
    )
    op.create_table(
        "paper_command_receipts",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("paper_order_id", sa.String(64), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ORDER_CREATED','REJECTED')", name="ck_paper_receipt_outcome"
        ),
        sa.CheckConstraint(
            "(outcome='ORDER_CREATED')=(paper_order_id IS NOT NULL)",
            name="ck_paper_receipt_order_presence",
        ),
    )
    op.create_table(
        "paper_authorization_attempts",
        sa.Column("authorization_id", sa.String(64), primary_key=True),
        sa.Column("authorization_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("namespace", sa.String(16), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("namespace='test'", name="ck_p4_authorization_test_only"),
        sa.CheckConstraint(
            "outcome IN ('CONSUMED_ORDER_CREATED','BLOCKED')", name="ck_paper_authorization_outcome"
        ),
    )
    op.create_table(
        "paper_broker_inputs",
        sa.Column("broker_seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('TEST_COMMAND','RECORDED_BOOK')", name="ck_p4_broker_input_kind"
        ),
    )
    op.create_table(
        "paper_orders",
        sa.Column("order_id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=False),
        sa.Column("authorization_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("time_in_force", sa.String(8), nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("limit_price", MONEY, nullable=False),
        sa.Column("filled_quantity", MONEY, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("accepted_broker_seq", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["accepted_broker_seq"], ["paper_broker_inputs.broker_seq"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("account_id", "client_order_id", name="uq_paper_client_order"),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_paper_order_symbol"),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_paper_order_side"),
        sa.CheckConstraint("order_type='LIMIT' AND time_in_force='GTC'", name="ck_paper_limit_gtc"),
        sa.CheckConstraint("quantity>0 AND limit_price>0", name="ck_paper_order_positive"),
        sa.CheckConstraint(
            "filled_quantity>=0 AND filled_quantity<=quantity", name="ck_paper_fill_bound"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','PARTIALLY_FILLED','FILLED','CANCELLED')",
            name="ck_paper_order_status",
        ),
    )
    op.create_table(
        "paper_order_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("order_version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("order_id", "order_version", name="uq_paper_order_version"),
    )
    op.create_table(
        "paper_fills",
        sa.Column("fill_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False, unique=True),
        sa.Column("broker_seq", sa.BigInteger(), nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("fee_asset", sa.String(8), nullable=False),
        sa.Column("fee_rate", MONEY, nullable=False),
        sa.Column("fee_amount", MONEY, nullable=False),
        sa.Column("fee_policy_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["broker_seq"], ["paper_broker_inputs.broker_seq"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("quantity>0 AND price>0", name="ck_paper_fill_positive"),
        sa.CheckConstraint(
            "fee_asset IN ('BTC','ETH','USDT') AND fee_rate>=0 AND fee_amount>=0",
            name="ck_paper_fill_fee",
        ),
    )
    op.create_table(
        "paper_inventory_lots",
        sa.Column("lot_id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("asset", sa.String(8), nullable=False),
        sa.Column("acquired_quantity", MONEY, nullable=False),
        sa.Column("quote_cost", MONEY, nullable=False),
        sa.Column("source_fill_id", sa.String(64), nullable=False, unique=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_fill_id"], ["paper_fills.fill_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("asset IN ('BTC','ETH')", name="ck_paper_lot_asset"),
        sa.CheckConstraint("acquired_quantity>0 AND quote_cost>=0", name="ck_paper_lot_values"),
    )
    op.create_table(
        "paper_lot_consumptions",
        sa.Column("consumption_id", sa.String(64), primary_key=True),
        sa.Column("lot_id", sa.String(64), nullable=False),
        sa.Column("source_fill_id", sa.String(64), nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("quote_basis", MONEY, nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["paper_inventory_lots.lot_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_fill_id"], ["paper_fills.fill_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("lot_id", "source_fill_id", name="uq_paper_lot_fill_consumption"),
        sa.CheckConstraint("quantity>0 AND quote_basis>=0", name="ck_paper_consumption_values"),
    )
    op.create_table(
        "paper_ledger_transactions",
        sa.Column("transaction_id", sa.String(64), primary_key=True),
        sa.Column("business_event_type", sa.String(64), nullable=False),
        sa.Column("business_event_id", sa.String(64), nullable=False),
        sa.Column("journal_kind", sa.String(16), nullable=False),
        sa.Column("reversal_of", sa.String(64), nullable=True),
        sa.Column("replacement_for", sa.String(64), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reversal_of"], ["paper_ledger_transactions.transaction_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replacement_for"], ["paper_ledger_transactions.transaction_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "business_event_type",
            "business_event_id",
            "journal_kind",
            name="uq_paper_business_journal",
        ),
        sa.CheckConstraint(
            "journal_kind IN ('PHYSICAL','VALUATION')", name="ck_paper_journal_kind"
        ),
        sa.CheckConstraint(
            "reversal_of IS NULL OR reversal_of<>transaction_id", name="ck_paper_no_self_reversal"
        ),
    )
    op.create_table(
        "paper_ledger_entries",
        sa.Column("transaction_id", sa.String(64), primary_key=True),
        sa.Column("line_no", sa.Integer(), primary_key=True),
        sa.Column("account_code", sa.String(64), nullable=False),
        sa.Column("commodity", sa.String(16), nullable=False),
        sa.Column("debit", MONEY, nullable=False),
        sa.Column("credit", MONEY, nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["paper_ledger_transactions.transaction_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("line_no>=0", name="ck_paper_ledger_line"),
        sa.CheckConstraint(
            "commodity IN ('BTC','ETH','USDT','USDT_VAL')", name="ck_paper_ledger_commodity"
        ),
        sa.CheckConstraint(
            "debit>=0 AND credit>=0 AND ((debit>0)<>(credit>0))", name="ck_paper_ledger_one_side"
        ),
    )
    op.create_table(
        "paper_reconciliation_checkpoints",
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("output_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mismatch_codes", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('HEALTHY','FAILED')", name="ck_paper_reconciliation_status"),
    )
    op.execute("""
        CREATE FUNCTION reject_paper_history_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Paper fill, lot and ledger history is append-only'; END;
        $$ LANGUAGE plpgsql
    """)
    for table in (
        "paper_command_receipts",
        "paper_authorization_attempts",
        "paper_broker_inputs",
        "paper_order_events",
        "paper_fills",
        "paper_inventory_lots",
        "paper_lot_consumptions",
        "paper_ledger_transactions",
        "paper_ledger_entries",
        "paper_reconciliation_checkpoints",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_paper_history_mutation()
        """)
    op.execute("""
        CREATE VIEW paper_ledger_balance_v1 AS
        SELECT transaction_id, commodity, sum(debit)-sum(credit) AS imbalance
        FROM paper_ledger_entries GROUP BY transaction_id, commodity
    """)
    op.execute("""
        CREATE FUNCTION assert_paper_ledger_balanced() RETURNS trigger AS $$
        DECLARE target_id varchar(64);
        BEGIN
          target_id := COALESCE(NEW.transaction_id, OLD.transaction_id);
          IF EXISTS (
            SELECT 1 FROM paper_ledger_entries
            WHERE transaction_id=target_id
            GROUP BY commodity HAVING sum(debit)<>sum(credit)
          ) THEN
            RAISE EXCEPTION 'Paper ledger transaction is not balanced per commodity';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER paper_ledger_entries_balanced
        AFTER INSERT OR UPDATE OR DELETE ON paper_ledger_entries
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION assert_paper_ledger_balanced()
    """)
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_paper_engine') THEN
            CREATE ROLE woozoo_paper_engine LOGIN;
          END IF;
        END $$
    """)
    tables = (
        "paper_policy_versions, paper_symbol_rule_versions, paper_accounts, paper_asset_balances, paper_command_receipts, "
        "paper_authorization_attempts, paper_broker_inputs, paper_orders, paper_order_events, "
        "paper_fills, paper_inventory_lots, paper_lot_consumptions, paper_ledger_transactions, "
        "paper_ledger_entries, paper_reconciliation_checkpoints"
    )
    op.execute(f"REVOKE ALL ON {tables} FROM PUBLIC")
    op.execute("REVOKE ALL ON paper_ledger_balance_v1 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_paper_engine")
    op.execute(f"GRANT SELECT ON {tables} TO woozoo_paper_engine")
    op.execute(
        "GRANT INSERT ON paper_policy_versions, paper_symbol_rule_versions, paper_accounts, paper_command_receipts, "
        "paper_authorization_attempts, paper_broker_inputs, paper_orders, paper_order_events, "
        "paper_fills, paper_inventory_lots, paper_lot_consumptions, paper_ledger_transactions, "
        "paper_ledger_entries, paper_reconciliation_checkpoints, outbox_events TO woozoo_paper_engine"
    )
    op.execute("GRANT INSERT, UPDATE ON paper_asset_balances, paper_orders TO woozoo_paper_engine")
    op.execute("GRANT SELECT ON paper_ledger_balance_v1 TO woozoo_paper_engine")
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE paper_broker_inputs_broker_seq_seq TO woozoo_paper_engine"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS paper_ledger_entries_balanced ON paper_ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS assert_paper_ledger_balanced")
    op.execute("DROP VIEW paper_ledger_balance_v1")
    for table in (
        "paper_reconciliation_checkpoints",
        "paper_ledger_entries",
        "paper_ledger_transactions",
        "paper_lot_consumptions",
        "paper_inventory_lots",
        "paper_fills",
        "paper_order_events",
        "paper_broker_inputs",
        "paper_authorization_attempts",
        "paper_command_receipts",
    ):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_paper_history_mutation")
    for table in (
        "paper_reconciliation_checkpoints",
        "paper_ledger_entries",
        "paper_ledger_transactions",
        "paper_lot_consumptions",
        "paper_inventory_lots",
        "paper_fills",
        "paper_order_events",
        "paper_orders",
        "paper_broker_inputs",
        "paper_authorization_attempts",
        "paper_command_receipts",
        "paper_asset_balances",
        "paper_accounts",
        "paper_policy_versions",
    ):
        op.drop_table(table)
    op.execute("DROP TABLE IF EXISTS paper_symbol_rule_versions")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_paper_engine') THEN
            REASSIGN OWNED BY woozoo_paper_engine TO CURRENT_USER;
            DROP OWNED BY woozoo_paper_engine;
            DROP ROLE woozoo_paper_engine;
          END IF;
        END $$
    """)
    op.execute("DROP INDEX IF EXISTS uq_paper_outbox_aggregate_version")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS aggregate_version")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS aggregate_id")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS aggregate_type")
