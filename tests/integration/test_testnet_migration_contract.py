from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "versions" / "20260722_0008_testnet_gateway.py"


def test_phase8_migration_is_parseable_and_follows_phase7() -> None:
    text = MIGRATION.read_text("utf-8")
    ast.parse(text)
    assert 'revision = "20260722_0008"' in text
    assert 'down_revision = "20260720_0007"' in text


def test_phase8_migration_has_separate_roles_and_authority_tables() -> None:
    text = MIGRATION.read_text("utf-8")
    assert "CREATE ROLE woozoo_testnet_execution LOGIN" in text
    assert "CREATE ROLE woozoo_spot_testnet_gateway LOGIN" in text
    for table in (
        "testnet_accounts",
        "testnet_account_generations",
        "testnet_activations",
        "testnet_activation_revocations",
        "testnet_safety_state",
        "testnet_operator_intents",
        "testnet_operator_intent_results",
        "testnet_domain_events",
        "testnet_outbox",
        "testnet_order_previews",
        "testnet_risk_decisions",
        "testnet_approvals",
        "testnet_approval_revocations",
        "testnet_execution_authorizations",
        "testnet_gateway_commands",
        "testnet_gateway_dispatch_attempts",
        "testnet_gateway_receipts",
        "testnet_gateway_observations",
        "testnet_orders",
        "testnet_fills",
        "testnet_ledger_transactions",
        "testnet_ledger_entries",
        "testnet_reconciliation_checkpoints",
    ):
        assert f'"{table}"' in text


def test_gateway_role_cannot_read_actor_risk_or_ledger_authority() -> None:
    text = MIGRATION.read_text("utf-8")
    for view in (
        "testnet_pending_gateway_commands_v1",
        "testnet_gateway_authority_v1",
        "testnet_gateway_receipt_reader_v1",
        "testnet_gateway_unresolved_dispatch_v1",
    ):
        assert view in text
    assert "testnet_gateway_unresolved_dispatch_v1 TO woozoo_spot_testnet_gateway" in text
    assert "GRANT INSERT ON testnet_gateway_inbox, testnet_gateway_dispatch_attempts" in text
    assert '"testnet_gateway_receipts, testnet_gateway_observations TO ' in text
    assert "woozoo_spot_testnet_gateway" in text
    for forbidden in (
        "GRANT SELECT ON local_operators TO woozoo_spot_testnet_gateway",
        "GRANT SELECT ON operator_sessions TO woozoo_spot_testnet_gateway",
        "GRANT SELECT ON testnet_risk_decisions TO woozoo_spot_testnet_gateway",
        "GRANT SELECT ON testnet_ledger_entries TO woozoo_spot_testnet_gateway",
        "GRANT UPDATE ON testnet_gateway_commands TO woozoo_spot_testnet_gateway",
        "GRANT DELETE",
    ):
        assert forbidden not in text


def test_paper_approval_cannot_satisfy_testnet_foreign_keys() -> None:
    text = MIGRATION.read_text("utf-8")
    assert '["approval_id"], ["testnet_approvals.approval_id"]' in text
    assert '["decision_id"], ["testnet_risk_decisions.decision_id"]' in text
    assert '["generation_id"], ["testnet_account_generations.generation_id"]' in text
    assert '["approval_id"], ["paper_approvals.approval_id"]' not in text


def test_phase8_immutability_decimal_and_unknown_constraints_are_present() -> None:
    text = MIGRATION.read_text("utf-8")
    assert "NUMERIC(38,18)" in text
    assert "SUBMISSION_UNKNOWN" in text
    assert "client_order_id ~ '^wz8-[a-f0-9]{32}$'" in text
    assert "reject_risk_history_mutation" in text
    assert 'ondelete="RESTRICT"' in text
    assert "RESET_SUSPECTED" in text
    assert "AUTHORIZATION_RECEIPT_MISMATCH" in text
    assert "testnet_operator_intent_receipts_v1" in text
    assert "GRANT INSERT ON testnet_operator_intents TO woozoo_control_api" in text
