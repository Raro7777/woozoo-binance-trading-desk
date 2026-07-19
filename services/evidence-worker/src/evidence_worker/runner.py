"""Closed application entry boundary for one deterministic Evidence materialization."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from .persistence import EvidenceCommandResult, PostgresEvidenceStore
from .settings import EvidenceSettings
from .types import EvidenceSnapshot


def materialize_evidence(
    environment: Mapping[str, str],
    *,
    symbol: str,
    as_of: datetime,
    knowledge_cutoff: datetime,
    created_at: datetime,
) -> tuple[EvidenceSnapshot, bool]:
    settings = EvidenceSettings.from_environment(environment)
    store = PostgresEvidenceStore(settings.database_url)
    snapshot = store.build_snapshot(
        symbol=symbol,
        as_of=as_of,
        knowledge_cutoff=knowledge_cutoff,
    )
    return snapshot, store.append_snapshot(snapshot, created_at=created_at)


def materialize_evidence_command(
    environment: Mapping[str, str],
    *,
    idempotency_key: str,
    service_principal: str,
    symbol: str,
    as_of: datetime,
    knowledge_cutoff: datetime,
    created_at: datetime,
) -> EvidenceCommandResult:
    settings = EvidenceSettings.from_environment(environment)
    return PostgresEvidenceStore(settings.database_url).materialize_command(
        idempotency_key=idempotency_key,
        service_principal=service_principal,
        symbol=symbol,
        as_of=as_of,
        knowledge_cutoff=knowledge_cutoff,
        created_at=created_at,
    )
