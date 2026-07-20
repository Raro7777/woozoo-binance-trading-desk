"""Monotonic Phase 5 Kill activation; intentionally contains no recovery path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re

import psycopg
from psycopg.types.json import Jsonb

from platform_core import canonical_hash


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")
_INVARIANT_REASONS = {
    "LEDGER_IMBALANCE",
    "PHYSICAL_LEDGER_MISMATCH",
    "AUTHORIZATION_RECEIPT_MISMATCH",
}


class KillPersistenceStage(StrEnum):
    RECEIPT = "receipt"
    EVENT = "event"
    STATE = "state"
    OUTBOX = "outbox"


@dataclass(frozen=True, slots=True)
class KillActivation:
    request_id: str
    expected_version: int
    trigger_kind: str
    actor_id: str
    reason_code: str
    reason: str
    observed_at: datetime
    context_digest: str
    scope: str = "paper-global"

    def validate(self) -> None:
        if self.scope != "paper-global" or self.expected_version < 0:
            raise ValueError("INVALID_KILL_SCOPE_OR_VERSION")
        if (
            _OPAQUE_ID.fullmatch(self.request_id) is None
            or _OPAQUE_ID.fullmatch(self.actor_id) is None
            or _HASH.fullmatch(self.context_digest) is None
            or self.observed_at.tzinfo is None
            or not 1 <= len(self.reason) <= 512
        ):
            raise ValueError("INVALID_KILL_ACTIVATION")
        if self.trigger_kind == "MANUAL":
            if self.reason_code != "MANUAL_SAFETY_STOP" or not self.actor_id.startswith(
                "operator:"
            ):
                raise ValueError("UNAUTHENTICATED_KILL_ACTOR")
        elif self.trigger_kind == "INVARIANT":
            if self.reason_code not in _INVARIANT_REASONS or not self.actor_id.startswith(
                "safety-service:"
            ):
                raise ValueError("UNALLOWLISTED_KILL_TRIGGER")
        else:
            raise ValueError("INVALID_KILL_TRIGGER")

    def request_material(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "request_id": self.request_id,
            "expected_version": self.expected_version,
            "trigger_kind": self.trigger_kind,
            "actor_id": self.actor_id,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "observed_at": self.observed_at.isoformat(),
            "context_digest": self.context_digest,
        }


@dataclass(frozen=True, slots=True)
class KillActivationResult:
    created: bool
    activation_event_id: str
    prior_version: int
    version: int
    request_hash: str
    outbox_event_id: str


class PostgresKillSwitch:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _fail(stage: KillPersistenceStage, requested: KillPersistenceStage | None) -> None:
        if stage == requested:
            raise RuntimeError(f"INJECTED_KILL_FAILURE:{stage.value}")

    def activate(
        self,
        command: KillActivation,
        *,
        _fail_after: KillPersistenceStage | None = None,
    ) -> KillActivationResult:
        command.validate()
        request_hash = canonical_hash(command.request_material())
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"kill-request:{command.request_id}",),
            )
            prior = connection.execute(
                "SELECT request_hash,activation_event_id,response "
                "FROM risk_kill_command_receipts WHERE request_id=%s",
                (command.request_id,),
            ).fetchone()
            if prior is not None:
                if prior[0] != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                response = prior[2]
                return KillActivationResult(
                    False,
                    prior[1],
                    response["prior_version"],
                    response["version"],
                    request_hash,
                    response["outbox_event_id"],
                )

            state = connection.execute(
                "SELECT active,version FROM kill_switch_state WHERE scope='paper-global' FOR UPDATE"
            ).fetchone()
            if state is None:
                raise RuntimeError("KILL_BARRIER_MISSING")
            active, prior_version = state
            if active:
                raise RuntimeError("KILL_SWITCH_ALREADY_ACTIVE")
            if prior_version != command.expected_version:
                raise ValueError("KILL_VERSION_CONFLICT")
            new_version = prior_version + 1
            event_material = {
                **command.request_material(),
                "prior_version": prior_version,
                "version": new_version,
            }
            activation_event_id = canonical_hash(event_material)
            data: dict[str, object] = {
                "scope": "paper-global",
                "active": True,
                "prior_version": prior_version,
                "version": new_version,
                "activation_event_id": activation_event_id,
                "trigger_kind": command.trigger_kind,
                "actor_id": command.actor_id,
                "reason_code": command.reason_code,
                "reason": command.reason,
                "observed_at": command.observed_at.isoformat(),
                "context_digest": command.context_digest,
            }
            event_type = "kill-switch.activated.v1"
            outbox_event_id = canonical_hash(
                ["event", event_type, activation_event_id, str(new_version)]
            )
            payload_hash = canonical_hash(data)
            envelope: dict[str, object] = {
                "spec_version": "woozoo.event/v1",
                "event_id": outbox_event_id,
                "event_type": event_type,
                "event_version": 1,
                "occurred_at": command.observed_at.isoformat(),
                "producer": "risk-engine",
                "activation_phase": 7,
                "aggregate_id": activation_event_id,
                "aggregate_version": new_version,
                "payload_hash": payload_hash,
                "data": data,
            }
            response = {
                "activation_event_id": activation_event_id,
                "prior_version": prior_version,
                "version": new_version,
                "outbox_event_id": outbox_event_id,
            }
            connection.execute(
                "INSERT INTO risk_kill_command_receipts"
                "(request_id,request_hash,activation_event_id,response,created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    command.request_id,
                    request_hash,
                    activation_event_id,
                    Jsonb(response),
                    command.observed_at,
                ),
            )
            self._fail(KillPersistenceStage.RECEIPT, _fail_after)
            connection.execute(
                "INSERT INTO kill_switch_events"
                "(activation_event_id,scope,request_id,request_hash,trigger_kind,actor_id,"
                "reason_code,reason,observed_at,context_digest,prior_version,new_version) "
                "VALUES (%s,'paper-global',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    activation_event_id,
                    command.request_id,
                    request_hash,
                    command.trigger_kind,
                    command.actor_id,
                    command.reason_code,
                    command.reason,
                    command.observed_at,
                    command.context_digest,
                    prior_version,
                    new_version,
                ),
            )
            self._fail(KillPersistenceStage.EVENT, _fail_after)
            updated = connection.execute(
                "UPDATE kill_switch_state SET active=true,version=%s,last_activation_event_id=%s,"
                "last_recovery_event_id=NULL "
                "WHERE scope='paper-global' AND active=false AND version=%s",
                (new_version, activation_event_id, prior_version),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KILL_STATE_TRANSITION_CONFLICT")
            self._fail(KillPersistenceStage.STATE, _fail_after)
            connection.execute(
                "INSERT INTO outbox_events"
                "(event_id,event_type,payload,payload_hash,occurred_at,aggregate_type,"
                "aggregate_id,aggregate_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    outbox_event_id,
                    event_type,
                    Jsonb(envelope),
                    payload_hash,
                    command.observed_at,
                    "kill_switch",
                    activation_event_id,
                    new_version,
                ),
            )
            persisted_outbox = connection.execute(
                "SELECT event_type,payload_hash,aggregate_type,aggregate_id,aggregate_version,payload "
                "FROM outbox_events WHERE event_id=%s",
                (outbox_event_id,),
            ).fetchone()
            if persisted_outbox != (
                event_type,
                payload_hash,
                "kill_switch",
                activation_event_id,
                new_version,
                envelope,
            ):
                raise ValueError("KILL_OUTBOX_IDEMPOTENCY_CONFLICT")
            connection.execute(
                "INSERT INTO risk_outbox_links(event_id,aggregate_kind,aggregate_id) "
                "VALUES (%s,'kill-switch',%s) ON CONFLICT DO NOTHING",
                (outbox_event_id, activation_event_id),
            )
            persisted_link = connection.execute(
                "SELECT aggregate_kind,aggregate_id FROM risk_outbox_links WHERE event_id=%s",
                (outbox_event_id,),
            ).fetchone()
            if persisted_link != ("kill-switch", activation_event_id):
                raise ValueError("KILL_OUTBOX_LINK_CONFLICT")
            self._fail(KillPersistenceStage.OUTBOX, _fail_after)
        return KillActivationResult(
            True,
            activation_event_id,
            prior_version,
            new_version,
            request_hash,
            outbox_event_id,
        )
