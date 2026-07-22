from pathlib import Path


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "db/migrations/versions/20260719_0004_paper_broker_ledger.py"


def test_phase_four_migration_closes_financial_and_activation_boundaries() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "sa.Numeric(38, 18)",
        "namespace='test'",
        "filled_quantity<=quantity",
        "paper_ledger_balance_v1",
        "reject_paper_history_mutation",
        "uq_paper_business_journal",
        "ck_paper_ledger_transaction_id",
        "assert_paper_relational_consistency",
        "assert_paper_correction_pair",
        "paper_migration_metadata",
        "UPDATE (available, held, version)",
        "UPDATE (filled_quantity, status, held_amount, version)",
        "woozoo_paper_engine",
    ):
        assert required in text
    assert "GRANT INSERT, UPDATE ON paper_asset_balances, paper_orders" not in text
    assert "DROP OWNED BY woozoo_paper_engine" not in text
    for forbidden in (
        "risk_decisions",
        "paper_approvals",
        "kill_switch",
        "external_orders",
        "api_key",
        "signature",
    ):
        assert forbidden not in text.lower()
