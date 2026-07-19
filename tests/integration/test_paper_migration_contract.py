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
        "woozoo_paper_engine",
    ):
        assert required in text
    for forbidden in (
        "risk_decisions",
        "paper_approvals",
        "kill_switch",
        "external_orders",
        "api_key",
        "signature",
    ):
        assert forbidden not in text.lower()
