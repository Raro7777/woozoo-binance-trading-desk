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


def _append_only(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_paper_history_mutation()
    """)


def _constraint_trigger(table: str, name: str, function: str, events: str) -> None:
    op.execute(f"""
        CREATE CONSTRAINT TRIGGER {name}
        AFTER {events} ON {table}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {function}()
    """)


def upgrade() -> None:
    # Phase 1 used UUIDs for the generic outbox. Paper domain identifiers are
    # content-addressed SHA-256 values, so widen the shared key without changing
    # any existing UUID value.
    op.drop_constraint(
        "outbox_delivery_attempts_event_id_fkey",
        "outbox_delivery_attempts",
        type_="foreignkey",
    )
    op.alter_column(
        "outbox_events",
        "event_id",
        existing_type=postgresql.UUID(as_uuid=False),
        type_=sa.String(64),
        postgresql_using="event_id::text",
    )
    op.alter_column(
        "outbox_delivery_attempts",
        "event_id",
        existing_type=postgresql.UUID(as_uuid=False),
        type_=sa.String(64),
        postgresql_using="event_id::text",
    )
    op.create_foreign_key(
        "outbox_delivery_attempts_event_id_fkey",
        "outbox_delivery_attempts",
        "outbox_events",
        ["event_id"],
        ["event_id"],
        ondelete="RESTRICT",
    )
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
        "paper_migration_metadata",
        sa.Column("migration_revision", sa.String(32), primary_key=True),
        sa.Column("writer_role_created", sa.Boolean(), nullable=False),
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
        "paper_outbox_links",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.event_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
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
        "paper_broker_inputs",
        sa.Column("broker_seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_quantity", MONEY, nullable=True),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("best_bid", MONEY, nullable=True),
        sa.Column("best_ask", MONEY, nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["paper_accounts.account_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("broker_seq", "source_key", name="uq_paper_broker_seq_source"),
        sa.CheckConstraint(
            "source_kind IN ('TEST_COMMAND','RECORDED_BOOK')", name="ck_p4_broker_input_kind"
        ),
        sa.CheckConstraint("length(payload_hash)=64", name="ck_paper_input_hash"),
        sa.CheckConstraint(
            "(source_kind='TEST_COMMAND' AND available_quantity IS NULL "
            "AND symbol IS NULL AND best_bid IS NULL AND best_ask IS NULL) OR "
            "(source_kind='RECORDED_BOOK' AND available_quantity>0 "
            "AND symbol IN ('BTCUSDT','ETHUSDT') AND best_bid>0 AND best_ask>0 "
            "AND best_bid<=best_ask)",
            name="ck_paper_input_liquidity",
        ),
    )
    op.create_table(
        "paper_command_receipts",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("paper_order_id", sa.String(64), nullable=True),
        sa.Column("authorization_id", sa.String(64), nullable=False, unique=True),
        sa.Column("broker_seq", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["paper_accounts.account_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["broker_seq"],
            ["paper_broker_inputs.broker_seq"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("length(request_hash)=64", name="ck_paper_receipt_hash"),
        sa.CheckConstraint(
            "outcome IN ('ORDER_CREATED','ORDER_CANCELLED','REJECTED')",
            name="ck_paper_receipt_outcome",
        ),
        sa.CheckConstraint(
            "(outcome IN ('ORDER_CREATED','ORDER_CANCELLED'))=(paper_order_id IS NOT NULL)",
            name="ck_paper_receipt_order_presence",
        ),
    )
    op.create_table(
        "paper_authorization_attempts",
        sa.Column("authorization_id", sa.String(64), primary_key=True),
        sa.Column("authorization_nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("command_scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("namespace", sa.String(16), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["paper_accounts.account_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["command_scope", "idempotency_key"],
            ["paper_command_receipts.scope", "paper_command_receipts.idempotency_key"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("command_scope", "idempotency_key", name="uq_paper_attempt_command"),
        sa.CheckConstraint("namespace='test'", name="ck_p4_authorization_test_only"),
        sa.CheckConstraint("length(request_hash)=64", name="ck_paper_attempt_hash"),
        sa.CheckConstraint(
            "outcome IN ('CONSUMED_ORDER_CREATED','CONSUMED_ORDER_CANCELLED','BLOCKED')",
            name="ck_paper_authorization_outcome",
        ),
        sa.CheckConstraint(
            "(outcome='BLOCKED')=(reason_code IS NOT NULL)", name="ck_paper_attempt_reason"
        ),
    )
    op.create_table(
        "paper_orders",
        sa.Column("order_id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("command_scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=False),
        sa.Column("authorization_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("time_in_force", sa.String(8), nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("limit_price", MONEY, nullable=False),
        sa.Column("filled_quantity", MONEY, nullable=False),
        sa.Column("held_asset", sa.String(8), nullable=False),
        sa.Column("held_amount", MONEY, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("accepted_broker_seq", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["accepted_broker_seq"],
            ["paper_broker_inputs.broker_seq"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_id"],
            ["paper_authorization_attempts.authorization_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["command_scope", "idempotency_key"],
            ["paper_command_receipts.scope", "paper_command_receipts.idempotency_key"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("account_id", "client_order_id", name="uq_paper_client_order"),
        sa.UniqueConstraint("command_scope", "idempotency_key", name="uq_paper_order_command"),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_paper_order_symbol"),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_paper_order_side"),
        sa.CheckConstraint("order_type='LIMIT' AND time_in_force='GTC'", name="ck_paper_limit_gtc"),
        sa.CheckConstraint("quantity>0 AND limit_price>0", name="ck_paper_order_positive"),
        sa.CheckConstraint(
            "filled_quantity>=0 AND filled_quantity<=quantity", name="ck_paper_fill_bound"
        ),
        sa.CheckConstraint("held_amount>=0", name="ck_paper_order_hold_nonnegative"),
        sa.CheckConstraint(
            "(side='BUY' AND held_asset='USDT') OR "
            "(side='SELL' AND held_asset=replace(symbol,'USDT',''))",
            name="ck_paper_order_hold_asset",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','PARTIALLY_FILLED','FILLED','CANCELLED')",
            name="ck_paper_order_status",
        ),
    )
    op.create_foreign_key(
        "fk_paper_receipt_attempt",
        "paper_command_receipts",
        "paper_authorization_attempts",
        ["authorization_id"],
        ["authorization_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_paper_receipt_order",
        "paper_command_receipts",
        "paper_orders",
        ["paper_order_id"],
        ["order_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "paper_order_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("order_version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("order_id", "order_version", name="uq_paper_order_version"),
        sa.UniqueConstraint("order_id", "source_key", name="uq_paper_order_event_source"),
        sa.CheckConstraint("order_version>0", name="ck_paper_order_event_version"),
    )
    op.create_table(
        "paper_fills",
        sa.Column("fill_id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("broker_seq", sa.BigInteger(), nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("fee_asset", sa.String(8), nullable=False),
        sa.Column("fee_rate", MONEY, nullable=False),
        sa.Column("fee_amount", MONEY, nullable=False),
        sa.Column("fee_policy_version", sa.String(64), nullable=False),
        sa.Column("symbol_rule_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["fee_policy_version"],
            ["paper_policy_versions.policy_version"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_key"],
            ["paper_broker_inputs.source_key"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("order_id", "source_key", name="uq_paper_order_fill_source"),
        sa.CheckConstraint("quantity>0 AND price>0", name="ck_paper_fill_positive"),
        sa.CheckConstraint(
            "fee_asset IN ('BTC','ETH','USDT') AND fee_rate>=0 AND fee_amount>=0",
            name="ck_paper_fill_fee",
        ),
        sa.CheckConstraint(
            "symbol_rule_version='spot-public-rules-2026-07-19'",
            name="ck_paper_fill_symbol_rule_version",
        ),
    )
    op.create_table(
        "paper_observation_effects",
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("effect_kind", sa.String(16), nullable=False),
        sa.Column("fill_id", sa.String(64), nullable=True, unique=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_key", "order_id"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_key"],
            ["paper_broker_inputs.source_key"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["fill_id"],
            ["paper_fills.fill_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "(effect_kind='FILL' AND fill_id IS NOT NULL) OR "
            "(effect_kind='NO_FILL' AND fill_id IS NULL)",
            name="ck_paper_observation_effect_kind",
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
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("business_event_type", sa.String(64), nullable=False),
        sa.Column("business_event_id", sa.String(64), nullable=False),
        sa.Column("journal_kind", sa.String(16), nullable=False),
        sa.Column("reversal_of", sa.String(64), nullable=True),
        sa.Column("replacement_for", sa.String(64), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reversal_of"],
            ["paper_ledger_transactions.transaction_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_for"],
            ["paper_ledger_transactions.transaction_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
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
            "NOT (reversal_of IS NOT NULL AND replacement_for IS NOT NULL)",
            name="ck_paper_single_correction_reference",
        ),
        sa.CheckConstraint(
            "reversal_of IS NULL OR reversal_of<>transaction_id", name="ck_paper_no_self_reversal"
        ),
        sa.CheckConstraint(
            "replacement_for IS NULL OR replacement_for<>transaction_id",
            name="ck_paper_no_self_replacement",
        ),
    )
    op.create_index(
        "uq_paper_single_reversal",
        "paper_ledger_transactions",
        ["reversal_of"],
        unique=True,
        postgresql_where=sa.text("reversal_of IS NOT NULL"),
    )
    op.create_index(
        "uq_paper_single_replacement",
        "paper_ledger_transactions",
        ["replacement_for"],
        unique=True,
        postgresql_where=sa.text("replacement_for IS NOT NULL"),
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
            "debit>=0 AND credit>=0 AND ((debit>0)<>(credit>0))",
            name="ck_paper_ledger_one_side",
        ),
    )
    op.create_table(
        "paper_reconciliation_checkpoints",
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("output_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mismatch_codes", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.account_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("account_id", "input_digest", name="uq_paper_reconciliation_input"),
        sa.CheckConstraint("status IN ('HEALTHY','FAILED')", name="ck_paper_reconciliation_status"),
    )

    # These immutable Phase 4 policies are part of the frozen oracle and public
    # Binance rule projection. Runtime writers may reference, but never mutate,
    # these exact versions.
    op.execute("""
        INSERT INTO paper_policy_versions
          (policy_version,fee_rate,participation_rate,quantity_step,payload_hash,created_at)
        VALUES
          ('quote-fee-v1',0.001,0.10,0.00000001,
           'e4ea0eb74588aefcae6d6a6e556d04964f56b4160a9231aa200ce71dfbe09040',
           '2026-07-19T00:00:00Z')
    """)
    op.execute("""
        INSERT INTO paper_symbol_rule_versions
          (rule_version,symbol,tick_size,step_size,min_quantity,min_notional,
           source_payload_hash,observed_at)
        VALUES
          ('spot-public-rules-2026-07-19','BTCUSDT',0.01,0.00001,0.00001,5,
           'c30f738ad7fd6d8e1378b9a8ef022c78c801cf4d1bc0b005f8870c188364a788',
           '2026-07-19T00:00:00Z'),
          ('spot-public-rules-2026-07-19','ETHUSDT',0.01,0.0001,0.0001,5,
           'c30f738ad7fd6d8e1378b9a8ef022c78c801cf4d1bc0b005f8870c188364a788',
           '2026-07-19T00:00:00Z')
    """)

    op.execute("""
        CREATE FUNCTION reject_paper_history_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Paper receipt, fill, lot and ledger history is append-only'; END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    for table in (
        "paper_policy_versions",
        "paper_symbol_rule_versions",
        "paper_command_receipts",
        "paper_authorization_attempts",
        "paper_broker_inputs",
        "paper_order_events",
        "paper_fills",
        "paper_observation_effects",
        "paper_inventory_lots",
        "paper_lot_consumptions",
        "paper_ledger_transactions",
        "paper_ledger_entries",
        "paper_reconciliation_checkpoints",
        "paper_outbox_links",
    ):
        _append_only(table)

    op.execute("""
        CREATE VIEW paper_ledger_balance_v1 AS
        SELECT transaction_id, commodity, sum(debit)-sum(credit) AS imbalance
        FROM paper_ledger_entries GROUP BY transaction_id, commodity
    """)
    op.execute("""
        CREATE VIEW paper_outbox_events_v1 AS
        SELECT link.account_id,event.event_id,event.event_type,event.aggregate_id,
               event.aggregate_version,event.payload_hash,event.payload,event.occurred_at
        FROM paper_outbox_links link JOIN outbox_events event USING(event_id)
    """)
    op.execute("""
        CREATE FUNCTION assert_paper_ledger_balanced() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM paper_ledger_transactions tx
            WHERE NOT EXISTS (
              SELECT 1 FROM paper_ledger_entries entry
              WHERE entry.transaction_id=tx.transaction_id
            )
          ) THEN
            RAISE EXCEPTION 'Paper ledger transaction requires at least one entry';
          END IF;
          IF EXISTS (
            SELECT 1 FROM paper_ledger_entries
            GROUP BY transaction_id, commodity HAVING sum(debit)<>sum(credit)
          ) THEN
            RAISE EXCEPTION 'Paper ledger transaction is not balanced per commodity';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    _constraint_trigger(
        "paper_ledger_transactions",
        "paper_ledger_transactions_balanced",
        "assert_paper_ledger_balanced",
        "INSERT OR UPDATE OR DELETE",
    )
    _constraint_trigger(
        "paper_ledger_entries",
        "paper_ledger_entries_balanced",
        "assert_paper_ledger_balanced",
        "INSERT OR UPDATE OR DELETE",
    )

    op.execute("""
        CREATE FUNCTION assert_paper_correction_pair() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM paper_ledger_transactions correction
            LEFT JOIN paper_ledger_transactions original
              ON original.transaction_id=COALESCE(correction.reversal_of,correction.replacement_for)
            WHERE
              (correction.reversal_of IS NOT NULL AND correction.business_event_type<>'paper.correction')
              OR (correction.replacement_for IS NOT NULL
                  AND correction.business_event_type<>'paper.replacement')
              OR ((correction.reversal_of IS NOT NULL OR correction.replacement_for IS NOT NULL)
                  AND (original.reversal_of IS NOT NULL OR original.replacement_for IS NOT NULL))
          ) THEN
            RAISE EXCEPTION 'Invalid Paper correction pair target or kind';
          END IF;
          IF EXISTS (
            SELECT 1 FROM paper_ledger_transactions reversal
            LEFT JOIN paper_ledger_transactions replacement
              ON replacement.replacement_for=reversal.reversal_of
             AND replacement.business_event_id=reversal.business_event_id
             AND replacement.journal_kind=reversal.journal_kind
             AND replacement.account_id=reversal.account_id
            WHERE reversal.reversal_of IS NOT NULL AND replacement.transaction_id IS NULL
          ) OR EXISTS (
            SELECT 1 FROM paper_ledger_transactions replacement
            LEFT JOIN paper_ledger_transactions reversal
              ON reversal.reversal_of=replacement.replacement_for
             AND reversal.business_event_id=replacement.business_event_id
             AND reversal.journal_kind=replacement.journal_kind
             AND reversal.account_id=replacement.account_id
            WHERE replacement.replacement_for IS NOT NULL AND reversal.transaction_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Paper correction pair requires one reversal and one replacement';
          END IF;
          IF EXISTS (
            SELECT 1 FROM paper_ledger_transactions reversal
            WHERE reversal.reversal_of IS NOT NULL AND (
              EXISTS (
                (SELECT line_no,account_code,commodity,debit,credit
                   FROM paper_ledger_entries WHERE transaction_id=reversal.transaction_id)
                EXCEPT
                (SELECT line_no,account_code,commodity,credit,debit
                   FROM paper_ledger_entries WHERE transaction_id=reversal.reversal_of)
              ) OR EXISTS (
                (SELECT line_no,account_code,commodity,credit,debit
                   FROM paper_ledger_entries WHERE transaction_id=reversal.reversal_of)
                EXCEPT
                (SELECT line_no,account_code,commodity,debit,credit
                   FROM paper_ledger_entries WHERE transaction_id=reversal.transaction_id)
              )
            )
          ) THEN
            RAISE EXCEPTION 'Paper correction reversal must exactly negate original entries';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    _constraint_trigger(
        "paper_ledger_transactions",
        "paper_ledger_transactions_correction_pair",
        "assert_paper_correction_pair",
        "INSERT OR UPDATE OR DELETE",
    )
    _constraint_trigger(
        "paper_ledger_entries",
        "paper_ledger_entries_correction_pair",
        "assert_paper_correction_pair",
        "INSERT OR UPDATE OR DELETE",
    )

    op.execute("""
        CREATE FUNCTION assert_paper_relational_consistency() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM paper_command_receipts receipt
            LEFT JOIN paper_authorization_attempts attempt
              ON attempt.authorization_id=receipt.authorization_id
            LEFT JOIN paper_broker_inputs input ON input.broker_seq=receipt.broker_seq
            LEFT JOIN paper_orders paper_order ON paper_order.order_id=receipt.paper_order_id
            WHERE attempt.authorization_id IS NULL OR input.broker_seq IS NULL
               OR attempt.account_id<>receipt.account_id
               OR attempt.command_scope<>receipt.scope
               OR attempt.idempotency_key<>receipt.idempotency_key
               OR attempt.request_hash<>receipt.request_hash
               OR input.account_id<>receipt.account_id OR input.source_kind<>'TEST_COMMAND'
               OR (receipt.outcome='ORDER_CREATED' AND (
                    attempt.outcome<>'CONSUMED_ORDER_CREATED' OR paper_order.order_id IS NULL
                    OR paper_order.account_id<>receipt.account_id
                    OR paper_order.authorization_id<>receipt.authorization_id
                    OR paper_order.command_scope<>receipt.scope
                    OR paper_order.idempotency_key<>receipt.idempotency_key
                    OR paper_order.accepted_broker_seq<>receipt.broker_seq))
               OR (receipt.outcome='ORDER_CANCELLED' AND (
                    attempt.outcome<>'CONSUMED_ORDER_CANCELLED'
                    OR paper_order.order_id IS NULL OR paper_order.status<>'CANCELLED'
                    OR paper_order.account_id<>receipt.account_id))
               OR (receipt.outcome='REJECTED' AND attempt.outcome<>'BLOCKED')
          ) OR EXISTS (
            SELECT 1 FROM paper_authorization_attempts attempt
            LEFT JOIN paper_command_receipts receipt
              ON receipt.scope=attempt.command_scope
             AND receipt.idempotency_key=attempt.idempotency_key
            WHERE receipt.scope IS NULL
          ) OR EXISTS (
            SELECT 1 FROM paper_orders paper_order
            LEFT JOIN paper_command_receipts receipt
              ON receipt.scope=paper_order.command_scope
             AND receipt.idempotency_key=paper_order.idempotency_key
            WHERE receipt.scope IS NULL OR receipt.paper_order_id<>paper_order.order_id
          ) THEN
            RAISE EXCEPTION 'Paper receipt/order/attempt/input consistency violation';
          END IF;
          IF EXISTS (
            SELECT 1 FROM paper_orders paper_order
            LEFT JOIN LATERAL (
              SELECT COALESCE(sum(quantity),0) AS filled FROM paper_fills fill
              WHERE fill.order_id=paper_order.order_id
            ) totals ON true
            WHERE totals.filled<>paper_order.filled_quantity
               OR (paper_order.status='OPEN' AND paper_order.filled_quantity<>0)
               OR (paper_order.status='PARTIALLY_FILLED' AND NOT (
                    paper_order.filled_quantity>0
                    AND paper_order.filled_quantity<paper_order.quantity))
               OR (paper_order.status='FILLED'
                    AND paper_order.filled_quantity<>paper_order.quantity)
               OR (paper_order.status='CANCELLED'
                    AND paper_order.filled_quantity>=paper_order.quantity)
               OR NOT EXISTS (
                    SELECT 1 FROM paper_symbol_rule_versions rule
                    WHERE rule.rule_version='spot-public-rules-2026-07-19'
                      AND rule.symbol=paper_order.symbol
                      AND mod(paper_order.limit_price,rule.tick_size)=0
                      AND mod(paper_order.quantity,rule.step_size)=0
                      AND paper_order.quantity>=rule.min_quantity
                      AND paper_order.quantity*paper_order.limit_price>=rule.min_notional)
          ) OR EXISTS (
            SELECT 1 FROM paper_fills fill
            JOIN paper_orders paper_order ON paper_order.order_id=fill.order_id
            JOIN paper_broker_inputs input
              ON input.source_key=fill.source_key
            JOIN paper_policy_versions policy
              ON policy.policy_version=fill.fee_policy_version
            WHERE fill.broker_seq<=paper_order.accepted_broker_seq
               OR input.broker_seq<=paper_order.accepted_broker_seq
               OR input.account_id<>paper_order.account_id
               OR input.source_kind<>'RECORDED_BOOK'
               OR input.symbol<>paper_order.symbol
               OR (paper_order.side='BUY' AND input.best_ask>paper_order.limit_price)
               OR (paper_order.side='SELL' AND input.best_bid<paper_order.limit_price)
               OR fill.fee_asset<>'USDT'
               OR fill.fee_rate<>policy.fee_rate
               OR fill.fee_amount<>
                  ceil(fill.quantity*fill.price*policy.fee_rate*1e18)/1e18
               OR NOT EXISTS (
                    SELECT 1 FROM paper_symbol_rule_versions rule
                    WHERE rule.rule_version=fill.symbol_rule_version
                      AND rule.symbol=paper_order.symbol
                      AND mod(paper_order.limit_price,rule.tick_size)=0
                      AND mod(paper_order.quantity,rule.step_size)=0
                      AND paper_order.quantity>=rule.min_quantity
                      AND paper_order.quantity*paper_order.limit_price>=rule.min_notional
                      AND mod(fill.quantity,rule.step_size)=0
                      AND fill.price=paper_order.limit_price)
          ) OR EXISTS (
            SELECT 1 FROM paper_broker_inputs input
            JOIN paper_policy_versions policy ON policy.policy_version='quote-fee-v1'
            WHERE input.source_kind='RECORDED_BOOK' AND (
              SELECT COALESCE(sum(fill.quantity),0) FROM paper_fills fill
              WHERE fill.source_key=input.source_key
            )>input.available_quantity*policy.participation_rate
          ) OR EXISTS (
            SELECT 1 FROM paper_fills fill
            JOIN paper_orders paper_order ON paper_order.order_id=fill.order_id
            WHERE NOT EXISTS (
              SELECT 1 FROM paper_ledger_transactions tx
              WHERE tx.business_event_type='paper.fill'
                AND tx.business_event_id=fill.fill_id AND tx.journal_kind='PHYSICAL'
                AND tx.account_id=paper_order.account_id
            ) OR NOT EXISTS (
              SELECT 1 FROM paper_ledger_transactions tx
              WHERE tx.business_event_type='paper.fill'
                AND tx.business_event_id=fill.fill_id AND tx.journal_kind='VALUATION'
                AND tx.account_id=paper_order.account_id
            ) OR (paper_order.side='BUY' AND NOT EXISTS (
              SELECT 1 FROM paper_inventory_lots lot WHERE lot.source_fill_id=fill.fill_id
            )) OR (paper_order.side='SELL' AND NOT EXISTS (
              SELECT 1 FROM paper_lot_consumptions item WHERE item.source_fill_id=fill.fill_id
            ))
          ) OR EXISTS (
            SELECT 1 FROM paper_fills fill
            JOIN paper_orders paper_order ON paper_order.order_id=fill.order_id
            LEFT JOIN LATERAL (
              SELECT count(entry.*) AS entry_count,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='paper.asset'
                    AND entry.commodity=replace(paper_order.symbol,'USDT','')),0) asset_debit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='exchange.clearing'
                    AND entry.commodity=replace(paper_order.symbol,'USDT','')),0) clearing_base_credit,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='exchange.clearing'
                    AND entry.commodity=replace(paper_order.symbol,'USDT','')),0) clearing_base_debit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='paper.held'
                    AND entry.commodity=replace(paper_order.symbol,'USDT','')),0) held_base_credit,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='exchange.clearing' AND entry.commodity='USDT'),0)
                  clearing_quote_debit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='exchange.clearing' AND entry.commodity='USDT'),0)
                  clearing_quote_credit,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='paper.available' AND entry.commodity='USDT'),0)
                  available_quote_debit,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='paper.fee' AND entry.commodity='USDT'),0)
                  fee_quote_debit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='paper.held' AND entry.commodity='USDT'),0)
                  held_quote_credit
              FROM paper_ledger_transactions tx
              JOIN paper_ledger_entries entry USING(transaction_id)
              WHERE tx.business_event_type='paper.fill'
                AND tx.business_event_id=fill.fill_id AND tx.journal_kind='PHYSICAL'
            ) physical ON true
            LEFT JOIN LATERAL (
              SELECT count(entry.*) AS entry_count,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='paper.inventory-basis'
                    AND entry.commodity='USDT_VAL'),0) inventory_debit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='paper.inventory-basis'
                    AND entry.commodity='USDT_VAL'),0) inventory_credit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='paper.acquisition-value'
                    AND entry.commodity='USDT_VAL'),0) acquisition_credit,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='paper.disposal-value'
                    AND entry.commodity='USDT_VAL'),0) disposal_debit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='paper.fee-value'
                    AND entry.commodity='USDT_VAL'),0) fee_value_credit,
                COALESCE(sum(entry.credit) FILTER (
                  WHERE entry.account_code='paper.realized-pnl'
                    AND entry.commodity='USDT_VAL'),0) pnl_credit,
                COALESCE(sum(entry.debit) FILTER (
                  WHERE entry.account_code='paper.realized-loss'
                    AND entry.commodity='USDT_VAL'),0) loss_debit
              FROM paper_ledger_transactions tx
              JOIN paper_ledger_entries entry USING(transaction_id)
              WHERE tx.business_event_type='paper.fill'
                AND tx.business_event_id=fill.fill_id AND tx.journal_kind='VALUATION'
            ) valuation ON true
            LEFT JOIN LATERAL (
              SELECT count(*) lot_count,COALESCE(sum(lot.acquired_quantity),0) quantity,
                     COALESCE(sum(lot.quote_cost),0) basis,
                     count(*) FILTER (WHERE lot.account_id<>paper_order.account_id
                       OR lot.asset<>replace(paper_order.symbol,'USDT','')) bad_count
              FROM paper_inventory_lots lot WHERE lot.source_fill_id=fill.fill_id
            ) bought ON true
            LEFT JOIN LATERAL (
              SELECT count(*) consumption_count,COALESCE(sum(item.quantity),0) quantity,
                     COALESCE(sum(item.quote_basis),0) basis,
                     count(*) FILTER (WHERE lot.account_id<>paper_order.account_id
                       OR lot.asset<>replace(paper_order.symbol,'USDT','')) bad_count
              FROM paper_lot_consumptions item
              JOIN paper_inventory_lots lot USING(lot_id)
              WHERE item.source_fill_id=fill.fill_id
            ) sold ON true
            WHERE (paper_order.side='BUY' AND (
                    physical.entry_count<>5
                    OR physical.asset_debit<>fill.quantity
                    OR physical.clearing_base_credit<>fill.quantity
                    OR physical.clearing_quote_debit<>fill.quantity*fill.price
                    OR physical.fee_quote_debit<>fill.fee_amount
                    OR physical.held_quote_credit<>fill.quantity*fill.price+fill.fee_amount
                    OR valuation.entry_count<>2
                    OR valuation.inventory_debit<>fill.quantity*fill.price+fill.fee_amount
                    OR valuation.acquisition_credit<>fill.quantity*fill.price+fill.fee_amount
                    OR bought.lot_count<>1 OR bought.bad_count<>0
                    OR bought.quantity<>fill.quantity
                    OR bought.basis<>fill.quantity*fill.price+fill.fee_amount
                    OR sold.consumption_count<>0))
               OR (paper_order.side='SELL' AND (
                    physical.entry_count<>5
                    OR physical.clearing_base_debit<>fill.quantity
                    OR physical.held_base_credit<>fill.quantity
                    OR physical.available_quote_debit<>fill.quantity*fill.price-fill.fee_amount
                    OR physical.fee_quote_debit<>fill.fee_amount
                    OR physical.clearing_quote_credit<>fill.quantity*fill.price
                    OR valuation.entry_count<>
                       CASE WHEN fill.quantity*fill.price=sold.basis+fill.fee_amount
                            THEN 3 ELSE 4 END
                    OR valuation.disposal_debit<>fill.quantity*fill.price
                    OR valuation.inventory_credit<>sold.basis
                    OR valuation.fee_value_credit<>fill.fee_amount
                    OR valuation.pnl_credit<>
                       greatest(fill.quantity*fill.price-sold.basis-fill.fee_amount,0)
                    OR valuation.loss_debit<>
                       greatest(sold.basis+fill.fee_amount-fill.quantity*fill.price,0)
                    OR sold.consumption_count=0 OR sold.bad_count<>0
                    OR sold.quantity<>fill.quantity OR bought.lot_count<>0))
          ) OR EXISTS (
            SELECT 1 FROM paper_observation_effects effect
            JOIN paper_orders paper_order ON paper_order.order_id=effect.order_id
            JOIN paper_broker_inputs input ON input.source_key=effect.source_key
            LEFT JOIN paper_fills fill ON fill.fill_id=effect.fill_id
            WHERE effect.account_id<>paper_order.account_id
               OR effect.account_id<>input.account_id
               OR input.source_kind<>'RECORDED_BOOK'
               OR input.broker_seq<=paper_order.accepted_broker_seq
               OR (effect.effect_kind='FILL' AND (
                    fill.fill_id IS NULL OR fill.order_id<>effect.order_id
                    OR fill.source_key<>effect.source_key))
               OR (effect.effect_kind='NO_FILL' AND EXISTS (
                    SELECT 1 FROM paper_fills existing
                    WHERE existing.order_id=effect.order_id
                      AND existing.source_key=effect.source_key))
               OR EXISTS (
                    SELECT 1 FROM paper_orders candidate
                    WHERE candidate.account_id=paper_order.account_id
                      AND candidate.symbol=paper_order.symbol
                      AND candidate.accepted_broker_seq<input.broker_seq
                      AND candidate.status NOT IN ('FILLED','CANCELLED')
                      AND (candidate.accepted_broker_seq,candidate.client_order_id,candidate.order_id)
                          < (paper_order.accepted_broker_seq,paper_order.client_order_id,
                             paper_order.order_id)
                      AND ((candidate.side='BUY' AND input.best_ask<=candidate.limit_price)
                        OR (candidate.side='SELL' AND input.best_bid>=candidate.limit_price))
                      AND NOT EXISTS (
                        SELECT 1 FROM paper_observation_effects prior_effect
                        WHERE prior_effect.source_key=effect.source_key
                          AND prior_effect.order_id=candidate.order_id))
          ) OR EXISTS (
            SELECT 1 FROM paper_fills fill
            WHERE NOT EXISTS (
              SELECT 1 FROM paper_observation_effects effect
              WHERE effect.fill_id=fill.fill_id AND effect.effect_kind='FILL')
          ) OR EXISTS (
            SELECT 1 FROM paper_inventory_lots lot
            LEFT JOIN LATERAL (
              SELECT COALESCE(sum(quantity),0) quantity,
                     COALESCE(sum(quote_basis),0) quote_basis
              FROM paper_lot_consumptions item WHERE item.lot_id=lot.lot_id
            ) used ON true
            WHERE used.quantity>lot.acquired_quantity OR used.quote_basis>lot.quote_cost
               OR (used.quantity=lot.acquired_quantity AND used.quote_basis<>lot.quote_cost)
          ) OR EXISTS (
            SELECT 1 FROM paper_orders paper_order
            LEFT JOIN LATERAL (
              SELECT count(*) AS event_count, COALESCE(max(order_version),0) AS max_version
              FROM paper_order_events event WHERE event.order_id=paper_order.order_id
            ) history ON true
            WHERE history.event_count<>paper_order.version OR history.max_version<>paper_order.version
          ) OR EXISTS (
            SELECT 1 FROM paper_order_events event
            LEFT JOIN outbox_events outbox
              ON outbox.aggregate_type='paper_order'
             AND outbox.aggregate_id=event.order_id
             AND outbox.aggregate_version=event.order_version
             AND outbox.event_type=event.event_type
            WHERE outbox.event_id IS NULL
          ) OR EXISTS (
            SELECT 1 FROM outbox_events outbox
            LEFT JOIN paper_outbox_links link USING(event_id)
            WHERE outbox.payload->>'producer'='paper-engine' AND link.event_id IS NULL
          ) OR EXISTS (
            SELECT 1 FROM paper_outbox_links link
            JOIN outbox_events outbox USING(event_id)
            WHERE (outbox.aggregate_type='paper_order' AND NOT EXISTS (
                    SELECT 1 FROM paper_orders paper_order
                    WHERE paper_order.order_id=outbox.aggregate_id
                      AND paper_order.account_id=link.account_id))
               OR (outbox.aggregate_type='paper_request' AND NOT EXISTS (
                    SELECT 1 FROM paper_command_receipts receipt
                    WHERE receipt.request_hash=outbox.aggregate_id
                      AND receipt.account_id=link.account_id))
               OR (outbox.aggregate_type='paper_authorization' AND NOT EXISTS (
                    SELECT 1 FROM paper_authorization_attempts attempt
                    WHERE attempt.authorization_id=outbox.payload->'data'->>'authorization_id'
                      AND attempt.account_id=link.account_id))
               OR (outbox.aggregate_type='paper_ledger' AND NOT EXISTS (
                    SELECT 1 FROM paper_ledger_transactions tx
                    WHERE tx.transaction_id=outbox.aggregate_id
                      AND tx.account_id=link.account_id))
          ) THEN
            RAISE EXCEPTION 'Paper fill/order consistency or durable outbox violation';
          END IF;
          IF EXISTS (
            SELECT 1 FROM paper_asset_balances balance
            LEFT JOIN LATERAL (
              SELECT COALESCE(sum(entry.debit-entry.credit),0) amount
              FROM paper_ledger_transactions tx
              JOIN paper_ledger_entries entry USING(transaction_id)
              WHERE tx.account_id=balance.account_id
                AND tx.journal_kind='PHYSICAL' AND entry.commodity=balance.asset
                AND entry.account_code IN ('paper.available','paper.asset')
            ) available ON true
            LEFT JOIN LATERAL (
              SELECT COALESCE(sum(entry.debit-entry.credit),0) amount
              FROM paper_ledger_transactions tx
              JOIN paper_ledger_entries entry USING(transaction_id)
              WHERE tx.account_id=balance.account_id
                AND tx.journal_kind='PHYSICAL' AND entry.commodity=balance.asset
                AND entry.account_code='paper.held'
            ) held ON true
            WHERE balance.available<>available.amount OR balance.held<>held.amount
          ) OR EXISTS (
            SELECT 1 FROM paper_ledger_transactions tx
            JOIN paper_ledger_entries entry USING(transaction_id)
            WHERE tx.journal_kind='PHYSICAL'
              AND entry.account_code IN ('paper.available','paper.asset','paper.held')
              AND NOT EXISTS (
                SELECT 1 FROM paper_asset_balances balance
                WHERE balance.account_id=tx.account_id AND balance.asset=entry.commodity)
          ) OR EXISTS (
            SELECT 1 FROM paper_orders paper_order
            WHERE NOT EXISTS (
              SELECT 1 FROM paper_asset_balances balance
              WHERE balance.account_id=paper_order.account_id
                AND balance.asset=paper_order.held_asset)
          ) THEN
            RAISE EXCEPTION 'Paper balance/ledger authority mismatch';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    for table in (
        "paper_command_receipts",
        "paper_authorization_attempts",
        "paper_broker_inputs",
        "paper_orders",
        "paper_order_events",
        "paper_fills",
        "paper_observation_effects",
        "paper_asset_balances",
        "paper_inventory_lots",
        "paper_lot_consumptions",
        "paper_ledger_transactions",
        "paper_ledger_entries",
        "paper_outbox_links",
        "outbox_events",
    ):
        _constraint_trigger(
            table,
            f"{table}_relational_consistency",
            "assert_paper_relational_consistency",
            "INSERT OR UPDATE OR DELETE",
        )

    op.execute("""
        CREATE FUNCTION append_paper_outbox(
          p_event_id varchar, p_event_type varchar, p_payload jsonb,
          p_payload_hash varchar, p_occurred_at timestamptz,
          p_aggregate_type varchar, p_aggregate_id varchar, p_aggregate_version bigint
        ) RETURNS void AS $$
        BEGIN
          IF p_event_type NOT IN (
               'paper.order.accepted.v1','paper.order.partially-filled.v1',
               'paper.order.filled.v1','paper.order.cancelled.v1',
               'paper.order.rejected.v1','paper.authorization.attempted.v1',
               'ledger.transaction.posted.v1')
             OR p_event_id !~ '^[a-f0-9]{64}$'
             OR p_payload_hash !~ '^[a-f0-9]{64}$'
             OR p_payload->>'event_id' IS DISTINCT FROM p_event_id
             OR p_payload->>'event_type' IS DISTINCT FROM p_event_type
             OR p_payload->>'payload_hash' IS DISTINCT FROM p_payload_hash
             OR p_payload->>'producer' IS DISTINCT FROM 'paper-engine'
             OR p_payload->>'spec_version' IS DISTINCT FROM 'woozoo.event/v1'
             OR p_payload->>'activation_phase' IS DISTINCT FROM '7'
             OR NOT (p_payload ?& ARRAY[
                  'spec_version','event_id','event_type','event_version','occurred_at',
                  'producer','activation_phase','aggregate_id','aggregate_version',
                  'payload_hash','data'])
             OR (SELECT count(*) FROM jsonb_object_keys(p_payload))<>11
             OR p_payload->>'aggregate_id' IS DISTINCT FROM p_aggregate_id
             OR p_aggregate_id !~ '^[a-f0-9]{64}$'
             OR jsonb_typeof(p_payload->'event_version')<>'number'
             OR jsonb_typeof(p_payload->'occurred_at')<>'string'
             OR jsonb_typeof(p_payload->'activation_phase')<>'number'
             OR jsonb_typeof(p_payload->'aggregate_version')<>'number'
             OR jsonb_typeof(p_payload->'data')<>'object'
          THEN
            RAISE EXCEPTION 'Invalid closed Paper outbox envelope';
          END IF;
          IF (p_payload->>'event_version')::bigint<>1
             OR (p_payload->>'occurred_at')::timestamptz IS DISTINCT FROM p_occurred_at
             OR (p_payload->>'aggregate_version')::bigint IS DISTINCT FROM p_aggregate_version
             OR p_aggregate_version<1
          THEN
            RAISE EXCEPTION 'Invalid closed Paper outbox envelope values';
          END IF;
          IF (p_event_type IN (
                'paper.order.accepted.v1','paper.order.partially-filled.v1',
                'paper.order.filled.v1','paper.order.cancelled.v1')
              AND p_aggregate_type IS DISTINCT FROM 'paper_order')
             OR (p_event_type='paper.order.rejected.v1'
                 AND p_aggregate_type IS DISTINCT FROM 'paper_request')
             OR (p_event_type='paper.authorization.attempted.v1'
                 AND p_aggregate_type IS DISTINCT FROM 'paper_authorization')
             OR (p_event_type='ledger.transaction.posted.v1'
                 AND p_aggregate_type IS DISTINCT FROM 'paper_ledger')
          THEN
            RAISE EXCEPTION 'Invalid closed Paper outbox aggregate type';
          END IF;
          IF (p_event_type='paper.order.accepted.v1' AND
                (NOT ((p_payload->'data') ? 'order_id') OR
                 jsonb_typeof(p_payload->'data'->'order_id')<>'string' OR
                 p_payload->'data'->>'order_id' !~ '^[a-f0-9]{64}$' OR
                 p_payload->'data'->>'order_id' IS DISTINCT FROM p_aggregate_id OR
                 (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))<>1))
             OR (p_event_type IN ('paper.order.partially-filled.v1','paper.order.filled.v1')
                 AND (NOT ((p_payload->'data') ?& ARRAY['order_id','fill_id']) OR
                      jsonb_typeof(p_payload->'data'->'order_id')<>'string' OR
                      jsonb_typeof(p_payload->'data'->'fill_id')<>'string' OR
                      p_payload->'data'->>'order_id' !~ '^[a-f0-9]{64}$' OR
                      p_payload->'data'->>'fill_id' !~ '^[a-f0-9]{64}$' OR
                      p_payload->'data'->>'order_id' IS DISTINCT FROM p_aggregate_id OR
                      (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))<>2))
             OR (p_event_type='paper.order.cancelled.v1' AND
                 (NOT ((p_payload->'data') ?& ARRAY['order_id','cancel_id']) OR
                  jsonb_typeof(p_payload->'data'->'order_id')<>'string' OR
                  jsonb_typeof(p_payload->'data'->'cancel_id')<>'string' OR
                  p_payload->'data'->>'order_id' !~ '^[a-f0-9]{64}$' OR
                  p_payload->'data'->>'order_id' IS DISTINCT FROM p_aggregate_id OR
                  length(p_payload->'data'->>'cancel_id')<1 OR
                  (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))<>2))
             OR (p_event_type='paper.order.rejected.v1' AND
                 (NOT ((p_payload->'data') ?& ARRAY['request_hash','reason']) OR
                  jsonb_typeof(p_payload->'data'->'request_hash')<>'string' OR
                  jsonb_typeof(p_payload->'data'->'reason')<>'string' OR
                  p_payload->'data'->>'request_hash' !~ '^[a-f0-9]{64}$' OR
                  p_payload->'data'->>'request_hash' IS DISTINCT FROM p_aggregate_id OR
                  p_payload->'data'->>'reason' IS DISTINCT FROM 'INSUFFICIENT_FUNDS' OR
                  (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))<>2))
             OR (p_event_type='paper.authorization.attempted.v1' AND
                 (NOT ((p_payload->'data') ?& ARRAY['authorization_id','outcome']) OR
                  jsonb_typeof(p_payload->'data'->'authorization_id')<>'string' OR
                  jsonb_typeof(p_payload->'data'->'outcome')<>'string' OR
                  length(p_payload->'data'->>'authorization_id')<1 OR
                  p_payload->'data'->>'outcome' NOT IN ('BLOCKED','CONSUMED') OR
                  (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))<>2))
             OR (p_event_type='ledger.transaction.posted.v1' AND
                 (NOT ((p_payload->'data') ? 'transaction_id') OR
                  jsonb_typeof(p_payload->'data'->'transaction_id')<>'string' OR
                  length(p_payload->'data'->>'transaction_id') NOT BETWEEN 1 AND 64 OR
                  p_payload->'data'->>'transaction_id' IS DISTINCT FROM p_aggregate_id OR
                  (SELECT count(*) FROM jsonb_object_keys(p_payload->'data'))<>1))
          THEN
            RAISE EXCEPTION 'Invalid closed Paper outbox event data';
          END IF;
          IF (p_event_type IN (
                'paper.order.accepted.v1','paper.order.partially-filled.v1',
                'paper.order.filled.v1','paper.order.cancelled.v1') AND
                NOT EXISTS (
                  SELECT 1 FROM paper_order_events event
                  WHERE event.order_id=p_aggregate_id
                    AND event.order_version=p_aggregate_version
                    AND event.event_type=p_event_type))
             OR (p_event_type='paper.order.rejected.v1' AND
                 NOT EXISTS (
                   SELECT 1 FROM paper_command_receipts receipt
                   WHERE receipt.request_hash=p_aggregate_id AND receipt.outcome='REJECTED'))
             OR (p_event_type='paper.authorization.attempted.v1' AND
                 NOT EXISTS (
                   SELECT 1 FROM paper_authorization_attempts attempt
                   WHERE attempt.authorization_id=p_payload->'data'->>'authorization_id'
                     AND ((p_payload->'data'->>'outcome'='BLOCKED'
                           AND attempt.outcome='BLOCKED')
                       OR (p_payload->'data'->>'outcome'='CONSUMED'
                           AND attempt.outcome LIKE 'CONSUMED_%'))))
             OR (p_event_type='ledger.transaction.posted.v1' AND
                 NOT EXISTS (
                   SELECT 1 FROM paper_ledger_transactions tx
                   WHERE tx.transaction_id=p_aggregate_id))
          THEN
            RAISE EXCEPTION 'Unlinked Paper outbox event';
          END IF;
          INSERT INTO outbox_events
            (event_id,event_type,payload,payload_hash,occurred_at,
             aggregate_type,aggregate_id,aggregate_version)
          VALUES
            (p_event_id,p_event_type,p_payload,p_payload_hash,p_occurred_at,
             p_aggregate_type,p_aggregate_id,p_aggregate_version);
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION append_paper_outbox(varchar,varchar,jsonb,varchar,"
        "timestamptz,varchar,varchar,bigint) FROM PUBLIC"
    )

    op.execute("""
        DO $$
        DECLARE role_created boolean := false;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_paper_engine') THEN
            CREATE ROLE woozoo_paper_engine LOGIN;
            role_created := true;
          END IF;
          INSERT INTO paper_migration_metadata(migration_revision,writer_role_created)
          VALUES ('20260719_0004',role_created);
        END $$
    """)
    tables = (
        "paper_policy_versions, paper_symbol_rule_versions, paper_accounts, paper_asset_balances, "
        "paper_command_receipts, paper_authorization_attempts, paper_broker_inputs, paper_orders, "
        "paper_order_events, paper_fills, paper_observation_effects, "
        "paper_inventory_lots, paper_lot_consumptions, "
        "paper_ledger_transactions, paper_ledger_entries, paper_reconciliation_checkpoints, "
        "paper_outbox_links"
    )
    op.execute(f"REVOKE ALL ON {tables} FROM PUBLIC")
    op.execute("REVOKE ALL ON paper_ledger_balance_v1 FROM PUBLIC")
    op.execute("REVOKE ALL ON paper_outbox_events_v1 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_paper_engine")
    op.execute(f"GRANT SELECT ON {tables} TO woozoo_paper_engine")
    op.execute(
        "GRANT INSERT ON paper_accounts, "
        "paper_command_receipts, paper_authorization_attempts, paper_broker_inputs, paper_orders, "
        "paper_order_events, paper_fills, paper_observation_effects, "
        "paper_inventory_lots, paper_lot_consumptions, "
        "paper_ledger_transactions, paper_ledger_entries, paper_reconciliation_checkpoints, "
        "paper_asset_balances, paper_outbox_links TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT UPDATE (available, held, version) ON paper_asset_balances TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT UPDATE (filled_quantity, status, held_amount, version) "
        "ON paper_orders TO woozoo_paper_engine"
    )
    op.execute("GRANT SELECT ON paper_ledger_balance_v1 TO woozoo_paper_engine")
    op.execute("GRANT SELECT ON paper_outbox_events_v1 TO woozoo_paper_engine")
    op.execute(
        "GRANT EXECUTE ON FUNCTION append_paper_outbox(varchar,varchar,jsonb,varchar,"
        "timestamptz,varchar,varchar,bigint) TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE paper_broker_inputs_broker_seq_seq TO woozoo_paper_engine"
    )


def downgrade() -> None:
    op.execute("""
        CREATE TEMP TABLE phase4_role_cleanup AS
        SELECT writer_role_created FROM paper_migration_metadata
        WHERE migration_revision='20260719_0004'
    """)
    op.execute("CREATE TEMP TABLE phase4_outbox_cleanup AS SELECT event_id FROM paper_outbox_links")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_paper_engine') THEN
            REVOKE ALL ON outbox_events FROM woozoo_paper_engine;
            REVOKE USAGE ON SCHEMA public FROM woozoo_paper_engine;
          END IF;
        END $$
    """)
    op.execute("DROP FUNCTION IF EXISTS assert_paper_relational_consistency CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_paper_history_mutation CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS append_paper_outbox(varchar,varchar,jsonb,varchar,"
        "timestamptz,varchar,varchar,bigint) CASCADE"
    )
    op.execute(
        "DELETE FROM paper_outbox_links WHERE event_id IN (SELECT event_id FROM phase4_outbox_cleanup)"
    )
    op.execute(
        "DELETE FROM outbox_events WHERE event_id IN (SELECT event_id FROM phase4_outbox_cleanup)"
    )
    op.execute("DROP FUNCTION IF EXISTS assert_paper_correction_pair CASCADE")
    op.execute("DROP FUNCTION IF EXISTS assert_paper_ledger_balanced CASCADE")
    op.execute("DROP VIEW IF EXISTS paper_ledger_balance_v1")
    op.execute("DROP VIEW IF EXISTS paper_outbox_events_v1")
    for table in (
        "paper_reconciliation_checkpoints",
        "paper_outbox_links",
        "paper_ledger_entries",
        "paper_ledger_transactions",
        "paper_lot_consumptions",
        "paper_inventory_lots",
        "paper_observation_effects",
        "paper_fills",
        "paper_order_events",
        "paper_orders",
        "paper_authorization_attempts",
        "paper_command_receipts",
        "paper_broker_inputs",
        "paper_asset_balances",
        "paper_symbol_rule_versions",
        "paper_accounts",
        "paper_policy_versions",
        "paper_migration_metadata",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_paper_outbox_aggregate_version")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS aggregate_version")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS aggregate_id")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS aggregate_type")
    op.drop_constraint(
        "outbox_delivery_attempts_event_id_fkey",
        "outbox_delivery_attempts",
        type_="foreignkey",
    )
    op.alter_column(
        "outbox_delivery_attempts",
        "event_id",
        existing_type=sa.String(64),
        type_=postgresql.UUID(as_uuid=False),
        postgresql_using="event_id::uuid",
    )
    op.alter_column(
        "outbox_events",
        "event_id",
        existing_type=sa.String(64),
        type_=postgresql.UUID(as_uuid=False),
        postgresql_using="event_id::uuid",
    )
    op.create_foreign_key(
        "outbox_delivery_attempts_event_id_fkey",
        "outbox_delivery_attempts",
        "outbox_events",
        ["event_id"],
        ["event_id"],
        ondelete="RESTRICT",
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM phase4_role_cleanup WHERE writer_role_created) THEN
            DROP ROLE woozoo_paper_engine;
          END IF;
        END $$
    """)
