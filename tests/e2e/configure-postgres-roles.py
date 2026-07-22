"""Assign disposable SCRAM credentials after the real migration creates roles."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql


ROLE_FILES = {
    "woozoo_control_reader": "E2E_CONTROL_READER_PASSWORD_FILE",
    "woozoo_control_api": "E2E_CONTROL_API_PASSWORD_FILE",
    "woozoo_market_writer": "E2E_MARKET_WRITER_PASSWORD_FILE",
    "woozoo_evidence_writer": "E2E_EVIDENCE_WRITER_PASSWORD_FILE",
    "woozoo_agent_orchestrator": "E2E_AGENT_ORCHESTRATOR_PASSWORD_FILE",
    "woozoo_risk_engine": "E2E_RISK_ENGINE_PASSWORD_FILE",
    "woozoo_paper_engine": "E2E_PAPER_ENGINE_PASSWORD_FILE",
    "woozoo_testnet_execution": "E2E_TESTNET_EXECUTION_PASSWORD_FILE",
    "woozoo_spot_testnet_gateway": "E2E_SPOT_TESTNET_GATEWAY_PASSWORD_FILE",
}


def _secret(variable: str) -> str:
    path = Path(os.environ[variable])
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise RuntimeError(f"{variable} must contain exactly one non-empty line")
    return value


def main() -> int:
    admin_url = os.environ.get("E2E_POSTGRES_ADMIN_URL", "").strip()
    if not admin_url:
        raise RuntimeError("E2E_POSTGRES_ADMIN_URL is required")
    with psycopg.connect(admin_url) as connection:
        encryption = connection.execute("SHOW password_encryption").fetchone()
        if encryption is None or encryption[0] != "scram-sha-256":
            raise RuntimeError("E2E PostgreSQL must create SCRAM credentials")
        for role, variable in ROLE_FILES.items():
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(_secret(variable))
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
