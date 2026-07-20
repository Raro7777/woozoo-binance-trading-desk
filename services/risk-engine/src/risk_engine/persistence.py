"""PostgreSQL persistence for immutable Phase 5 Risk decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from platform_core import canonical_hash

from .engine import evaluate_risk
from .models import RiskDecision


class RiskPersistenceStage(StrEnum):
    DECISION = "decision"
    OUTBOX = "outbox"


@dataclass(frozen=True, slots=True)
class PersistedRiskDecision:
    created: bool
    decision_id: str
    risk_input_digest: str
    decision_hash: str
    outbox_event_id: str


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("INVALID_RISK_INPUT")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("INVALID_DECISION_CLOCK")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("INVALID_DECISION_CLOCK")
    return parsed


def _event_id(event_type: str, aggregate_id: str, version: int) -> str:
    return canonical_hash(["event", event_type, aggregate_id, str(version)])


class PostgresRiskStore:
    """Commits decision state and its durable outbox in one local transaction."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _fail(stage: RiskPersistenceStage, requested: RiskPersistenceStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_RISK_FAILURE:{stage.value}")

    def persist_decision(
        self,
        risk_input: dict[str, object],
        decision: RiskDecision,
        *,
        recorded_at: datetime,
        _fail_after: RiskPersistenceStage | None = None,
    ) -> PersistedRiskDecision:
        if recorded_at.tzinfo is None:
            raise ValueError("RECORDED_AT_MUST_BE_AWARE")
        authoritative_decision = evaluate_risk(risk_input)
        if authoritative_decision != decision:
            raise ValueError("FORGED_RISK_DECISION")
        if canonical_hash(risk_input) != decision.risk_input_digest:
            raise ValueError("RISK_INPUT_DIGEST_MISMATCH")
        expected_decision_hash = canonical_hash(
            {
                "decision_schema_version": decision.decision_schema_version,
                "risk_input_digest": decision.risk_input_digest,
                "verdict": decision.verdict,
                "ordered_reason_codes": list(decision.ordered_reason_codes),
            }
        )
        if expected_decision_hash != decision.decision_hash:
            raise ValueError("DECISION_HASH_MISMATCH")

        proposal = _object(risk_input["proposal"])
        portfolio = _object(risk_input["portfolio"])
        data_state = _object(risk_input["data"])
        preview = _object(risk_input["order_preview"])
        policy = _object(risk_input["policy"])
        kill = _object(risk_input["kill_switch"])
        reconciliation = _object(risk_input["reconciliation"])
        clock = _object(risk_input["decision_clock"])
        decision_id = canonical_hash(["risk-decision", decision.decision_hash])
        event_type = "risk.decision.recorded.v1"
        event_id = _event_id(event_type, decision_id, 1)
        event_data: dict[str, object] = {
            "decision_schema_version": decision.decision_schema_version,
            "decision_id": decision_id,
            "risk_input_digest": decision.risk_input_digest,
            "decision_hash": decision.decision_hash,
            "verdict": decision.verdict,
            "primary_reason": decision.primary_reason_code,
            "ordered_reason_codes": list(decision.ordered_reason_codes),
            "policy_version": policy["version"],
            "proposal_hash": proposal["proposal_hash"],
            "portfolio_snapshot_hash": portfolio["snapshot_hash"],
            "data_state_hash": canonical_hash(data_state),
            "paper_order_preview_hash": preview["paper_order_preview_hash"],
            "reconciliation_checkpoint_hash": reconciliation["checkpoint_hash"],
            "kill_switch_version": kill["version"],
            "decision_as_of": clock["decision_as_of"],
        }
        payload_hash = canonical_hash(event_data)
        envelope: dict[str, object] = {
            "spec_version": "woozoo.event/v1",
            "event_id": event_id,
            "event_type": event_type,
            "event_version": 1,
            "occurred_at": recorded_at.isoformat(),
            "producer": "risk-engine",
            "activation_phase": 7,
            "aggregate_id": decision_id,
            "aggregate_version": 1,
            "payload_hash": payload_hash,
            "data": event_data,
        }

        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"risk-input:{decision.risk_input_digest}",),
            )
            prior = connection.execute(
                "SELECT decision_id,decision_hash FROM risk_decisions WHERE risk_input_digest=%s",
                (decision.risk_input_digest,),
            ).fetchone()
            if prior is not None:
                if prior != (decision_id, decision.decision_hash):
                    raise ValueError("RISK_INPUT_IDEMPOTENCY_CONFLICT")
                prior_event = connection.execute(
                    "SELECT event_id FROM risk_outbox_links WHERE aggregate_kind='risk-decision' "
                    "AND aggregate_id=%s",
                    (decision_id,),
                ).fetchone()
                if prior_event is None:
                    raise RuntimeError("RISK_DECISION_OUTBOX_MISSING")
                return PersistedRiskDecision(
                    False,
                    decision_id,
                    decision.risk_input_digest,
                    decision.decision_hash,
                    prior_event[0],
                )

            connection.execute(
                "INSERT INTO risk_decisions"
                "(decision_id,risk_input_digest,risk_input,decision_hash,verdict,primary_reason,"
                "ordered_reason_codes,policy_version,proposal_hash,portfolio_snapshot_hash,"
                "data_state_hash,paper_order_preview_hash,reconciliation_checkpoint_hash,"
                "kill_switch_version,decision_as_of,recorded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    decision_id,
                    decision.risk_input_digest,
                    Jsonb(risk_input),
                    decision.decision_hash,
                    decision.verdict,
                    decision.primary_reason_code,
                    Jsonb(list(decision.ordered_reason_codes)),
                    policy["version"],
                    proposal["proposal_hash"],
                    portfolio["snapshot_hash"],
                    canonical_hash(data_state),
                    preview["paper_order_preview_hash"],
                    reconciliation["checkpoint_hash"],
                    cast(int, kill["version"]),
                    _timestamp(clock["decision_as_of"]),
                    recorded_at,
                ),
            )
            self._fail(RiskPersistenceStage.DECISION, _fail_after)
            connection.execute(
                "INSERT INTO outbox_events"
                "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
                "aggregate_id,aggregate_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    event_id,
                    event_type,
                    Jsonb(envelope),
                    payload_hash,
                    recorded_at,
                    "risk_decision",
                    decision_id,
                    1,
                ),
            )
            persisted_outbox = connection.execute(
                "SELECT event_type,payload_hash,aggregate_type,aggregate_id,aggregate_version,payload "
                "FROM outbox_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()
            if persisted_outbox != (
                event_type,
                payload_hash,
                "risk_decision",
                decision_id,
                1,
                envelope,
            ):
                raise ValueError("RISK_OUTBOX_IDEMPOTENCY_CONFLICT")
            connection.execute(
                "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
                "VALUES (%s,'risk-decision',%s) ON CONFLICT DO NOTHING",
                (event_id, decision_id),
            )
            persisted_link = connection.execute(
                "SELECT aggregate_kind,aggregate_id FROM risk_outbox_links WHERE event_id=%s",
                (event_id,),
            ).fetchone()
            if persisted_link != ("risk-decision", decision_id):
                raise ValueError("RISK_OUTBOX_LINK_CONFLICT")
            self._fail(RiskPersistenceStage.OUTBOX, _fail_after)
        return PersistedRiskDecision(
            True, decision_id, decision.risk_input_digest, decision.decision_hash, event_id
        )
