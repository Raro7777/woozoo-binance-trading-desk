"""Create Phase 2 public market-data history and read projections."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260719_0002"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None


SOURCE = "binance_spot_public"
ALLOWLIST_VERSION = "binance-spot-public.v1@29c227d84058dd2be3fe3b42ab368d1d1ce910e5"


def upgrade() -> None:
    op.create_table(
        "collector_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=128), nullable=False),
        sa.Column("allowlist_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "source", "connection_id", name="uq_collector_sessions_source_connection"
        ),
        sa.CheckConstraint(f"source = '{SOURCE}'", name="ck_collector_sessions_public_source"),
        sa.CheckConstraint(
            f"allowlist_version = '{ALLOWLIST_VERSION}'", name="ck_collector_sessions_allowlist"
        ),
        sa.CheckConstraint(
            "status IN ('healthy','degraded','stale','invalid','reconnecting')",
            name="ck_collector_sessions_status",
        ),
    )
    op.create_table(
        "raw_market_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("collector_session_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("record_kind", sa.String(length=32), nullable=False),
        sa.Column("parent_raw_event_id", sa.String(length=64), nullable=True),
        sa.Column("source_dedupe_key", sa.String(length=192), nullable=False),
        sa.Column("payload_bytes", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("source_event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=True),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["collector_session_id"], ["collector_sessions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_raw_event_id"], ["raw_market_events.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "source", "stream", "symbol", "source_dedupe_key", name="uq_raw_market_source_dedupe"
        ),
        sa.CheckConstraint(f"source = '{SOURCE}'", name="ck_raw_market_public_source"),
        sa.CheckConstraint(
            "symbol IS NULL OR symbol IN ('BTCUSDT','ETHUSDT')", name="ck_raw_market_symbol"
        ),
        sa.CheckConstraint(
            "record_kind IN ('rest_response','rest_item','stream_message','control')",
            name="ck_raw_market_record_kind",
        ),
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_raw_market_source_dedupe_null_safe
        ON raw_market_events (source, stream, COALESCE(symbol, ''), source_dedupe_key)
    """)
    op.create_table(
        "normalized_market_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("raw_event_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("quality_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("stream_watermark", postgresql.JSONB(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_market_events.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "event_type",
            "symbol",
            "sequence",
            "raw_payload_hash",
            "schema_version",
            name="uq_normalized_market_dedupe",
        ),
        sa.CheckConstraint(f"source = '{SOURCE}'", name="ck_normalized_market_public_source"),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_normalized_market_symbol"),
        sa.CheckConstraint(
            "event_type IN ('trade','book_ticker','kline')", name="ck_normalized_market_type"
        ),
        sa.CheckConstraint(
            "quality_status IN ('healthy','degraded','stale','invalid','reconnecting')",
            name="ck_normalized_market_quality",
        ),
    )
    op.create_table(
        "data_quality_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_event_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_market_events.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("scope", "sequence_no", name="uq_data_quality_scope_sequence"),
        sa.CheckConstraint(
            "status IN ('healthy','degraded','stale','invalid','reconnecting')",
            name="ck_data_quality_status",
        ),
    )
    op.create_table(
        "stream_watermark_projections",
        sa.Column("collector_session_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("stream", sa.String(length=128), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["collector_session_id"], ["collector_sessions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "market_status_projections",
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("quality_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("stream_watermark", postgresql.JSONB(), nullable=False),
        sa.Column("last_event_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_event_id"], ["normalized_market_events.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_market_status_symbol"),
    )
    op.execute("""
        CREATE FUNCTION reject_market_history_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'market history is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("raw_market_events", "normalized_market_events", "data_quality_events"):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_market_history_mutation()
        """)
    op.execute("""
        CREATE VIEW market_status_reader_v1 AS
        SELECT symbol, price::text AS price, event_time, received_at, quality_status,
               quality_reasons, stream_watermark
        FROM market_status_projections
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_market_writer') THEN
                CREATE ROLE woozoo_market_writer LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_reader') THEN
                CREATE ROLE woozoo_control_reader LOGIN;
            END IF;
        END $$
    """)
    op.execute(
        "REVOKE ALL ON raw_market_events, normalized_market_events, data_quality_events, collector_sessions, stream_watermark_projections, market_status_projections FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON market_status_reader_v1 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_market_writer, woozoo_control_reader")
    op.execute(
        "GRANT SELECT, INSERT ON raw_market_events, normalized_market_events, data_quality_events TO woozoo_market_writer"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON collector_sessions, stream_watermark_projections, market_status_projections TO woozoo_market_writer"
    )
    op.execute("GRANT SELECT, INSERT ON outbox_events TO woozoo_market_writer")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO woozoo_market_writer")
    op.execute("GRANT SELECT ON market_status_reader_v1 TO woozoo_control_reader")


def downgrade() -> None:
    op.execute("DROP VIEW market_status_reader_v1")
    for table in ("data_quality_events", "normalized_market_events", "raw_market_events"):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_market_history_mutation")
    op.drop_table("market_status_projections")
    op.drop_table("stream_watermark_projections")
    op.drop_table("data_quality_events")
    op.drop_table("normalized_market_events")
    op.drop_table("raw_market_events")
    op.drop_table("collector_sessions")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_market_writer') THEN
                EXECUTE 'REVOKE ALL ON outbox_events FROM woozoo_market_writer';
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM woozoo_market_writer';
                DROP ROLE woozoo_market_writer;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_control_reader') THEN
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM woozoo_control_reader';
                DROP ROLE woozoo_control_reader;
            END IF;
        END $$
    """)
