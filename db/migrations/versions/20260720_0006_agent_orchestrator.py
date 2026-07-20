"""Create dormant Phase 6 Evidence-bound analysis and TradeProposal authority."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260720_0006"
down_revision = "20260719_0005"
branch_labels = None
depends_on = None


def _append_only(table: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_agent_history_mutation()
    """)


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_agent_history_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Phase 6 analysis and Proposal history is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.create_table(
        "agent_prompt_manifests",
        sa.Column("prompt_manifest_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("template_hash", sa.String(64), nullable=False),
        sa.Column("response_schema_id", sa.String(128), nullable=False),
        sa.Column("tool_allowlist", postgresql.JSONB(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", "role", name="uq_agent_prompt_version_role"),
        sa.CheckConstraint(
            "role IN ('MARKET_REGIME','TECHNICAL','TRADE_FLOW','BULL','BEAR',"
            "'TRADER','PORTFOLIO','AUDIT')",
            name="ck_agent_prompt_role",
        ),
        sa.CheckConstraint("tool_allowlist='[]'::jsonb", name="ck_agent_prompt_tools_empty"),
        sa.CheckConstraint(
            "prompt_manifest_id ~ '^[a-f0-9]{64}$' AND "
            "template_hash ~ '^[a-f0-9]{64}$' AND manifest_hash ~ '^[a-f0-9]{64}$'",
            name="ck_agent_prompt_hashes",
        ),
    )
    op.create_table(
        "analysis_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("namespace", sa.String(16), nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_version", sa.String(64), nullable=False),
        sa.Column("workflow_hash", sa.String(64), nullable=False),
        sa.Column("prompt_manifest_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("hold_reason", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence_snapshots.evidence_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("namespace='test'", name="ck_analysis_run_namespace"),
        sa.CheckConstraint("symbol IN ('BTCUSDT','ETHUSDT')", name="ck_analysis_run_symbol"),
        sa.CheckConstraint("provider='mock'", name="ck_analysis_run_provider"),
        sa.CheckConstraint("model='woozoo-deterministic-mock/v1'", name="ck_analysis_run_model"),
        sa.CheckConstraint("outcome IN ('COMPLETED','HOLD')", name="ck_analysis_run_outcome"),
        sa.CheckConstraint(
            "(outcome='COMPLETED' AND hold_reason IS NULL) OR "
            "(outcome='HOLD' AND hold_reason IS NOT NULL)",
            name="ck_analysis_run_hold_reason",
        ),
        sa.CheckConstraint("as_of<=knowledge_cutoff", name="ck_analysis_run_time"),
        sa.CheckConstraint(
            "run_id ~ '^[a-f0-9]{64}$' AND evidence_digest ~ '^[a-f0-9]{64}$' AND "
            "workflow_hash ~ '^[a-f0-9]{64}$' AND prompt_manifest_hash ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_run_hashes",
        ),
    )
    op.create_table(
        "agent_reports",
        sa.Column("report_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("report_version", sa.String(16), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence_snapshots.evidence_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("run_id", "role", "report_version", name="uq_agent_report_role"),
        sa.CheckConstraint(
            "role IN ('MARKET_REGIME','TECHNICAL','TRADE_FLOW','BULL','BEAR',"
            "'TRADER','PORTFOLIO','AUDIT')",
            name="ck_agent_report_role",
        ),
        sa.CheckConstraint(
            "report_id ~ '^[a-f0-9]{64}$' AND report_hash ~ '^[a-f0-9]{64}$'",
            name="ck_agent_report_hashes",
        ),
    )
    op.create_table(
        "agent_report_evidence_refs",
        sa.Column("report_id", sa.String(64), primary_key=True),
        sa.Column("evidence_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(["report_id"], ["agent_reports.report_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "trade_proposals",
        sa.Column("proposal_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column("proposal_version", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("risk_eligible", sa.Boolean(), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence_snapshots.evidence_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("run_id", "proposal_version", name="uq_trade_proposal_run_version"),
        sa.CheckConstraint("side IN ('BUY','SELL','HOLD')", name="ck_trade_proposal_side"),
        sa.CheckConstraint(
            "(side='HOLD' AND risk_eligible=false) OR "
            "(side IN ('BUY','SELL') AND risk_eligible=true)",
            name="ck_trade_proposal_risk_eligible",
        ),
        sa.CheckConstraint(
            "proposal_id ~ '^[a-f0-9]{64}$' AND proposal_hash ~ '^[a-f0-9]{64}$'",
            name="ck_trade_proposal_hashes",
        ),
    )
    op.create_table(
        "trade_proposal_evidence_refs",
        sa.Column("proposal_id", sa.String(64), primary_key=True),
        sa.Column("evidence_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["trade_proposals.proposal_id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "analysis_audit_records",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("audit_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("verdict IN ('ACCEPTED','HOLD')", name="ck_analysis_audit_verdict"),
        sa.CheckConstraint(
            "audit_id ~ '^[a-f0-9]{64}$' AND audit_hash ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_audit_hashes",
        ),
    )
    op.create_table(
        "analysis_run_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "event_type IN ('analysis.run.completed.v1','analysis.run.held.v1',"
            "'trade.proposal.created.v1')",
            name="ck_analysis_event_type",
        ),
        sa.CheckConstraint(
            "event_id ~ '^[a-f0-9]{64}$' AND payload_hash ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_event_hashes",
        ),
    )
    op.create_table(
        "agent_outbox_links",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.event_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
    )
    op.add_column("risk_decisions", sa.Column("proposal_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_risk_decisions_phase6_proposal",
        "risk_decisions",
        "trade_proposals",
        ["proposal_id"],
        ["proposal_id"],
        ondelete="RESTRICT",
    )
    op.execute(r"""
        DO $$
        DECLARE
          definition text;
          replaced text;
        BEGIN
          definition := pg_get_functiondef(
            'assert_kill_activation_consistency()'::regprocedure
          );
          replaced := regexp_replace(
            definition,
            'decision\.risk_input->>''risk_input_schema_version''\s+IS DISTINCT FROM ''woozoo\.risk-input/v1''',
            'decision.risk_input->>''risk_input_schema_version'' NOT IN (''woozoo.risk-input/v1'',''woozoo.risk-input/v2'')'
          );
          IF replaced=definition THEN
            RAISE EXCEPTION 'Phase 6 could not widen the frozen Risk consistency oracle';
          END IF;
          EXECUTE replaced;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION enforce_phase6_risk_proposal() RETURNS trigger AS $$
        BEGIN
          IF NEW.risk_input->>'risk_input_schema_version'='woozoo.risk-input/v1' THEN
            IF NEW.proposal_id IS NOT NULL THEN
              RAISE EXCEPTION 'Risk v1 cannot bind a Phase 6 Proposal row';
            END IF;
          ELSIF NEW.risk_input->>'risk_input_schema_version'='woozoo.risk-input/v2' THEN
            IF NEW.proposal_id IS NULL OR NOT EXISTS (
              SELECT 1 FROM trade_proposals proposal
              WHERE proposal.proposal_id=NEW.proposal_id
                AND proposal.proposal_hash=NEW.proposal_hash
                AND proposal.payload=NEW.risk_input->'proposal'->'payload'
                AND proposal.risk_eligible=true
                AND proposal.side IN ('BUY','SELL')
            ) THEN
              RAISE EXCEPTION 'Risk v2 Proposal binding is incomplete';
            END IF;
          ELSE
            RAISE EXCEPTION 'Risk input version is not approved';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER risk_decisions_phase6_proposal
        AFTER INSERT ON risk_decisions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_phase6_risk_proposal()
    """)
    op.execute("""
        CREATE FUNCTION enforce_agent_evidence_ref() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM evidence_items
            WHERE evidence_id=NEW.evidence_id AND item_id=NEW.item_id
          ) THEN
            RAISE EXCEPTION 'Phase 6 orphan Evidence citation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("agent_report_evidence_refs", "trade_proposal_evidence_refs"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {table}_evidence_member
            AFTER INSERT ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_agent_evidence_ref()
        """)
    for table in (
        "agent_prompt_manifests",
        "analysis_runs",
        "agent_reports",
        "agent_report_evidence_refs",
        "trade_proposals",
        "trade_proposal_evidence_refs",
        "analysis_audit_records",
        "analysis_run_events",
        "agent_outbox_links",
    ):
        _append_only(table)
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='woozoo_agent_orchestrator') THEN
            CREATE ROLE woozoo_agent_orchestrator LOGIN;
          END IF;
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO woozoo_agent_orchestrator")
    op.execute(
        "GRANT SELECT ON evidence_reader_v1, evidence_candle_reader_v1, "
        "evidence_feature_reader_v1 TO woozoo_agent_orchestrator"
    )
    op.execute(
        "GRANT SELECT, INSERT ON agent_prompt_manifests, analysis_runs, agent_reports, "
        "agent_report_evidence_refs, trade_proposals, trade_proposal_evidence_refs, "
        "analysis_audit_records, analysis_run_events, agent_outbox_links, outbox_events "
        "TO woozoo_agent_orchestrator"
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM analysis_runs LIMIT 1) THEN
            RAISE EXCEPTION 'Phase 6 immutable analysis history exists; downgrade refused';
          END IF;
        END $$
    """)
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM woozoo_agent_orchestrator")
    op.execute("DROP TRIGGER risk_decisions_phase6_proposal ON risk_decisions")
    op.execute("DROP FUNCTION enforce_phase6_risk_proposal()")
    op.execute(r"""
        DO $$
        DECLARE
          definition text;
          replaced text;
        BEGIN
          definition := pg_get_functiondef(
            'assert_kill_activation_consistency()'::regprocedure
          );
          replaced := regexp_replace(
            definition,
            'decision\.risk_input->>''risk_input_schema_version''\s+NOT IN \(''woozoo\.risk-input/v1'',''woozoo\.risk-input/v2''\)',
            'decision.risk_input->>''risk_input_schema_version'' IS DISTINCT FROM ''woozoo.risk-input/v1'''
          );
          IF replaced=definition THEN
            RAISE EXCEPTION 'Phase 6 could not restore the frozen Risk consistency oracle';
          END IF;
          EXECUTE replaced;
        END $$
    """)
    op.drop_constraint("fk_risk_decisions_phase6_proposal", "risk_decisions", type_="foreignkey")
    op.drop_column("risk_decisions", "proposal_id")
    for table in (
        "agent_outbox_links",
        "analysis_run_events",
        "analysis_audit_records",
        "trade_proposal_evidence_refs",
        "trade_proposals",
        "agent_report_evidence_refs",
        "agent_reports",
        "analysis_runs",
        "agent_prompt_manifests",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION enforce_agent_evidence_ref()")
    op.execute("DROP FUNCTION reject_agent_history_mutation()")
