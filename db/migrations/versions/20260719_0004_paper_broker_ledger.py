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
            "outcome IN ('CONSUMED_ORDER_CREATED','BLOCKED')", name="ck_paper_authorization_outcome"
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.order_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["broker_seq", "source_key"],
            ["paper_broker_inputs.broker_seq", "paper_broker_inputs.source_key"],
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

    op.execute("""
        CREATE FUNCTION reject_paper_history_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Paper receipt, fill, lot and ledger history is append-only'; END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
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
        _append_only(table)

    op.execute("""
        CREATE VIEW paper_ledger_balance_v1 AS
        SELECT transaction_id, commodity, sum(debit)-sum(credit) AS imbalance
        FROM paper_ledger_entries GROUP BY transaction_id, commodity
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
          ) OR EXISTS (
            SELECT 1 FROM paper_fills fill
            JOIN paper_orders paper_order ON paper_order.order_id=fill.order_id
            JOIN paper_broker_inputs input
              ON input.broker_seq=fill.broker_seq AND input.source_key=fill.source_key
            WHERE fill.broker_seq<=paper_order.accepted_broker_seq
               OR input.account_id<>paper_order.account_id
               OR input.source_kind<>'RECORDED_BOOK'
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
          ) THEN
            RAISE EXCEPTION 'Paper fill/order consistency or durable outbox violation';
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
        "outbox_events",
    ):
        _constraint_trigger(
            table,
            f"{table}_relational_consistency",
            "assert_paper_relational_consistency",
            "INSERT OR UPDATE OR DELETE",
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
        "paper_order_events, paper_fills, paper_inventory_lots, paper_lot_consumptions, "
        "paper_ledger_transactions, paper_ledger_entries, paper_reconciliation_checkpoints"
    )
    op.execute(f"REVOKE ALL ON {tables} FROM PUBLIC")
    op.execute("REVOKE ALL ON paper_ledger_balance_v1 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_paper_engine")
    op.execute(f"GRANT SELECT ON {tables} TO woozoo_paper_engine")
    op.execute(
        "GRANT INSERT ON paper_policy_versions, paper_symbol_rule_versions, paper_accounts, "
        "paper_command_receipts, paper_authorization_attempts, paper_broker_inputs, paper_orders, "
        "paper_order_events, paper_fills, paper_inventory_lots, paper_lot_consumptions, "
        "paper_ledger_transactions, paper_ledger_entries, paper_reconciliation_checkpoints, "
        "outbox_events TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT UPDATE (available, held, version) ON paper_asset_balances TO woozoo_paper_engine"
    )
    op.execute(
        "GRANT UPDATE (filled_quantity, status, held_amount, version) "
        "ON paper_orders TO woozoo_paper_engine"
    )
    op.execute("GRANT SELECT ON paper_ledger_balance_v1 TO woozoo_paper_engine")
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE paper_broker_inputs_broker_seq_seq TO woozoo_paper_engine"
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_paper_engine') THEN
            REVOKE ALL ON outbox_events FROM woozoo_paper_engine;
            REVOKE USAGE ON SCHEMA public FROM woozoo_paper_engine;
          END IF;
        END $$
    """)
    op.execute("DROP FUNCTION IF EXISTS assert_paper_relational_consistency CASCADE")
    op.execute(
        "DELETE FROM outbox_events WHERE aggregate_type='paper_order' OR event_type LIKE 'paper.%'"
    )
    op.execute("DROP FUNCTION IF EXISTS assert_paper_correction_pair CASCADE")
    op.execute("DROP FUNCTION IF EXISTS assert_paper_ledger_balanced CASCADE")
    op.execute("DROP VIEW IF EXISTS paper_ledger_balance_v1")
    op.execute("DROP FUNCTION IF EXISTS reject_paper_history_mutation CASCADE")
    for table in (
        "paper_reconciliation_checkpoints",
        "paper_ledger_entries",
        "paper_ledger_transactions",
        "paper_lot_consumptions",
        "paper_inventory_lots",
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
