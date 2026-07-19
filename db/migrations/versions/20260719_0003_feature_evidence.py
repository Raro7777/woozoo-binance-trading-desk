"""Create Phase 3 append-only feature and point-in-time Evidence history."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260719_0003"
down_revision = "20260719_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_observations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("feature_name", sa.String(length=64), nullable=False),
        sa.Column("feature_version", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_feature_symbol"),
        sa.CheckConstraint("interval IN ('1m','5m','1h','4h')", name="ck_feature_interval"),
        sa.CheckConstraint("id ~ '^[a-f0-9]{64}$'", name="ck_feature_id_digest"),
        sa.CheckConstraint("input_digest ~ '^[a-f0-9]{64}$'", name="ck_feature_input_digest"),
        sa.UniqueConstraint(
            "symbol",
            "interval",
            "feature_name",
            "feature_version",
            "as_of",
            "input_digest",
            name="uq_feature_observation_identity",
        ),
    )
    op.create_table(
        "feature_observation_inputs",
        sa.Column("feature_observation_id", sa.String(length=64), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("normalized_event_id", sa.String(length=64), nullable=False),
        sa.Column("raw_event_id", sa.String(length=64), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["feature_observation_id"], ["feature_observations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["normalized_event_id"], ["normalized_market_events.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_market_events.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("ordinal >= 0", name="ck_feature_input_ordinal"),
        sa.CheckConstraint(
            "raw_payload_hash ~ '^[a-f0-9]{64}$'", name="ck_feature_input_payload_hash"
        ),
        sa.UniqueConstraint(
            "feature_observation_id",
            "normalized_event_id",
            name="uq_feature_observation_normalized_input",
        ),
    )
    op.create_index(
        "uq_feature_input_provenance",
        "feature_observation_inputs",
        ["feature_observation_id", "raw_event_id", "raw_payload_hash"],
        unique=True,
    )
    op.create_index(
        "uq_normalized_evidence_provenance",
        "normalized_market_events",
        ["id", "raw_event_id", "raw_payload_hash"],
        unique=True,
    )
    op.create_table(
        "evidence_snapshots",
        sa.Column("evidence_id", sa.String(length=64), primary_key=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipe_version", sa.String(length=64), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("quality_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("collector_session_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("watermark_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["collector_session_id"], ["collector_sessions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_evidence_symbol"),
        sa.CheckConstraint(
            "quality_status = 'healthy'",
            name="ck_evidence_quality",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(quality_reasons) = 'array' AND jsonb_array_length(quality_reasons) = 0",
            name="ck_evidence_quality_reasons_empty",
        ),
        sa.CheckConstraint(
            "recipe_version = 'woozoo.evidence.closed-candles-approved-features/v1'",
            name="ck_evidence_recipe",
        ),
        sa.CheckConstraint("evidence_id ~ '^[a-f0-9]{64}$'", name="ck_evidence_id_digest"),
        sa.CheckConstraint(
            "evidence_digest ~ '^[a-f0-9]{64}$'", name="ck_evidence_digest"
        ),
        sa.CheckConstraint("input_digest ~ '^[a-f0-9]{64}$'", name="ck_evidence_input_digest"),
        sa.CheckConstraint(
            "watermark_digest ~ '^[a-f0-9]{64}$'", name="ck_evidence_watermark_digest"
        ),
    )
    op.create_table(
        "evidence_items",
        sa.Column("evidence_id", sa.String(length=64), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("normalized_event_id", sa.String(length=64), nullable=True),
        sa.Column("feature_observation_id", sa.String(length=64), nullable=True),
        sa.Column("raw_event_id", sa.String(length=64), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence_snapshots.evidence_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_market_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["normalized_event_id", "raw_event_id", "raw_payload_hash"],
            [
                "normalized_market_events.id",
                "normalized_market_events.raw_event_id",
                "normalized_market_events.raw_payload_hash",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["feature_observation_id", "raw_event_id", "raw_payload_hash"],
            [
                "feature_observation_inputs.feature_observation_id",
                "feature_observation_inputs.raw_event_id",
                "feature_observation_inputs.raw_payload_hash",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_evidence_item_ordinal"),
        sa.CheckConstraint(
            "item_type IN ('normalized_market_event','feature_observation')",
            name="ck_evidence_item_type",
        ),
        sa.CheckConstraint(
            "(item_type='normalized_market_event' AND normalized_event_id=item_id "
            "AND feature_observation_id IS NULL) OR "
            "(item_type='feature_observation' AND feature_observation_id=item_id "
            "AND normalized_event_id IS NULL)",
            name="ck_evidence_item_typed_source",
        ),
        sa.CheckConstraint("item_id ~ '^[a-f0-9]{64}$'", name="ck_evidence_item_id_digest"),
        sa.CheckConstraint(
            "raw_payload_hash ~ '^[a-f0-9]{64}$'", name="ck_evidence_item_payload_hash"
        ),
        sa.UniqueConstraint(
            "evidence_id",
            "item_type",
            "item_id",
            "raw_event_id",
            name="uq_evidence_item_provenance",
        ),
    )
    op.create_table(
        "evidence_command_receipts",
        sa.Column("idempotency_key", sa.String(length=128), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence_snapshots.evidence_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[a-f0-9]{64}$'", name="ck_evidence_command_request_hash"
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_feature_evidence_history_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'feature and evidence history is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in (
        "feature_observations",
        "feature_observation_inputs",
        "evidence_snapshots",
        "evidence_items",
        "evidence_command_receipts",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_feature_evidence_history_mutation()
        """)
    op.execute("""
        CREATE VIEW evidence_reader_v1 AS
        SELECT snapshot.evidence_id, snapshot.evidence_digest, snapshot.symbol,
               snapshot.as_of, snapshot.knowledge_cutoff, snapshot.recipe_version,
               snapshot.input_digest, snapshot.quality_status, snapshot.quality_reasons,
               snapshot.collector_session_id, snapshot.watermark_digest,
               item.ordinal AS item_ordinal, item.item_type, item.item_id,
               item.raw_event_id, item.raw_payload_hash
        FROM evidence_snapshots AS snapshot
        JOIN evidence_items AS item ON item.evidence_id = snapshot.evidence_id
    """)
    op.execute("""
        CREATE VIEW evidence_candle_reader_v1 AS
        SELECT item.evidence_id, item.ordinal, normalized.id AS normalized_event_id,
               normalized.raw_event_id, normalized.raw_payload_hash,
               normalized.event_time, normalized.received_at, normalized.payload
        FROM evidence_items AS item
        JOIN normalized_market_events AS normalized
          ON normalized.id=item.normalized_event_id
        WHERE item.item_type='normalized_market_event'
    """)
    op.execute("""
        CREATE VIEW evidence_feature_reader_v1 AS
        SELECT item.evidence_id, min(item.ordinal) AS ordinal,
               feature.id AS feature_id, feature.feature_name,
               feature.feature_version, feature.interval, feature.as_of,
               feature.value_text, feature.input_digest
        FROM evidence_items AS item
        JOIN feature_observations AS feature
          ON feature.id=item.feature_observation_id
        WHERE item.item_type='feature_observation'
        GROUP BY item.evidence_id, feature.id, feature.feature_name,
                 feature.feature_version, feature.interval, feature.as_of,
                 feature.value_text, feature.input_digest
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_evidence_writer') THEN
                CREATE ROLE woozoo_evidence_writer LOGIN;
            END IF;
        END $$
    """)
    op.execute(
        "REVOKE ALL ON feature_observations, feature_observation_inputs, "
        "evidence_snapshots, evidence_items, evidence_command_receipts FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON evidence_reader_v1 FROM PUBLIC")
    op.execute("REVOKE ALL ON evidence_candle_reader_v1, evidence_feature_reader_v1 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_evidence_writer")
    op.execute(
        "GRANT SELECT ON normalized_market_events, data_quality_events "
        "TO woozoo_evidence_writer"
    )
    op.execute(
        "GRANT SELECT (id, collector_session_id, source, schema_version) ON raw_market_events "
        "TO woozoo_evidence_writer"
    )
    op.execute(
        "GRANT SELECT (id, allowlist_version, status) ON collector_sessions "
        "TO woozoo_evidence_writer"
    )
    op.execute(
        "GRANT INSERT ON feature_observations, feature_observation_inputs, "
        "evidence_snapshots, evidence_items, evidence_command_receipts, outbox_events "
        "TO woozoo_evidence_writer"
    )
    op.execute(
        "GRANT SELECT ON feature_observations, feature_observation_inputs, "
        "evidence_snapshots, evidence_command_receipts TO woozoo_evidence_writer"
    )
    op.execute(
        "GRANT SELECT ON evidence_reader_v1, evidence_candle_reader_v1, "
        "evidence_feature_reader_v1 TO woozoo_control_reader"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT ON evidence_reader_v1, evidence_candle_reader_v1, "
        "evidence_feature_reader_v1 FROM woozoo_control_reader"
    )
    op.execute("DROP VIEW evidence_feature_reader_v1")
    op.execute("DROP VIEW evidence_candle_reader_v1")
    op.execute("DROP VIEW evidence_reader_v1")
    for table in (
        "evidence_items",
        "evidence_command_receipts",
        "evidence_snapshots",
        "feature_observation_inputs",
        "feature_observations",
    ):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_feature_evidence_history_mutation")
    op.drop_table("evidence_command_receipts")
    op.drop_table("evidence_items")
    op.drop_table("evidence_snapshots")
    op.drop_index("uq_normalized_evidence_provenance", table_name="normalized_market_events")
    op.drop_index("uq_feature_input_provenance", table_name="feature_observation_inputs")
    op.drop_table("feature_observation_inputs")
    op.drop_table("feature_observations")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_evidence_writer') THEN
                EXECUTE 'REVOKE ALL ON outbox_events FROM woozoo_evidence_writer';
                EXECUTE 'REVOKE ALL ON normalized_market_events, raw_market_events, collector_sessions, data_quality_events FROM woozoo_evidence_writer';
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM woozoo_evidence_writer';
                DROP ROLE woozoo_evidence_writer;
            END IF;
        END $$
    """)
