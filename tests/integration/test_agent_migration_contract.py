from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_phase6_migration_is_append_only_least_privilege_and_test_only() -> None:
    source = (ROOT / "db/migrations/versions/20260720_0006_agent_orchestrator.py").read_text(
        "utf-8"
    )
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
        assert f'"{table}"' in source
    assert "reject_agent_history_mutation" in source
    assert "namespace='test'" in source
    assert "fk_risk_decisions_phase6_proposal" in source
    assert "evidence_reader_v1" in source
    assert "GRANT SELECT ON risk_decisions" not in source
    assert "GRANT SELECT ON paper_orders" not in source
    assert "immutable analysis history exists; downgrade refused" in source
