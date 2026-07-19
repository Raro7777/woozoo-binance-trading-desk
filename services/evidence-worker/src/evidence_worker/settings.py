"""Fail-closed Phase 3 evidence-worker process settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from psycopg.conninfo import conninfo_to_dict


class EvidenceSettingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceSettings:
    database_url: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> EvidenceSettings:
        if environment.get("TRADING_MODE") != "paper":
            raise EvidenceSettingsError("evidence-worker requires TRADING_MODE=paper")
        database_url = environment.get("EVIDENCE_DATABASE_URL", "").strip()
        if not database_url:
            raise EvidenceSettingsError("EVIDENCE_DATABASE_URL is required")
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise EvidenceSettingsError("EVIDENCE_DATABASE_URL must be a Postgres URL")
        if conninfo_to_dict(database_url).get("user") != "woozoo_evidence_writer":
            raise EvidenceSettingsError(
                "EVIDENCE_DATABASE_URL must use the dedicated woozoo_evidence_writer role"
            )
        return cls(database_url=database_url)
