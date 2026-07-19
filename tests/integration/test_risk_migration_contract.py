from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260719_0005_risk_engine.py"


def test_risk_migration_001_closes_authority_and_barrier_boundaries() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "risk_input_digest",
        "unique=True",
        "risk_decisions",
        "risk_outbox_links",
        "kill_switch_events",
        "kill_switch_state",
        "paper-global",
        "paper_lock_kill_barrier",
        "FOR SHARE",
        "SECURITY DEFINER SET search_path = pg_catalog, public",
        "REVOKE ALL ON FUNCTION paper_lock_kill_barrier() FROM PUBLIC",
        "paper_kill_inbox",
        "paper_kill_cancel_batches",
        "paper_kill_cancel_items",
        "cancelled_count BETWEEN 1 AND 100",
        "woozoo_risk_engine",
    ):
        assert required in text
    assert "GRANT UPDATE ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "GRANT INSERT ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "GRANT DELETE ON kill_switch_state TO woozoo_paper_engine" not in text
    assert "RECOVERY" not in text
    for forbidden in ("api_key", "signature", "testnet", "mainnet", "approval"):
        assert forbidden not in text.lower()
