"""Postgres transaction authority for Phase 8 operator intents and commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .canonical import CanonicalValue, canonical_digest
from .commands import build_existing_order_command


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("POSTGRES_INTEGER_INVALID")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("POSTGRES_TIMESTAMP_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeBinding:
    gateway_instance_id: str
    build_digest: str
    configuration_digest: str
    allowlist_digest: str


class PostgresIntentWorker:
    """Consumes one durable intent with domain state and outbox in one transaction."""

    def __init__(self, database_url: str, binding: ExecutionRuntimeBinding) -> None:
        self._database_url = database_url
        self._binding = binding

    def run_once(self, *, now: datetime | None = None) -> dict[str, object] | None:
        authority_at = (now or datetime.now(UTC)).astimezone(UTC)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            intent = connection.execute(
                "SELECT intent.* FROM testnet_operator_intents intent "
                "LEFT JOIN testnet_operator_intent_results result USING(intent_id) "
                "WHERE result.intent_id IS NULL ORDER BY intent.created_at,intent.intent_id "
                "LIMIT 1"
            ).fetchone()
            if intent is None:
                return None
            locked = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                (intent["intent_id"],),
            ).fetchone()
            if locked is None or locked["pg_try_advisory_xact_lock"] is not True:
                return None
            completed = connection.execute(
                "SELECT 1 FROM testnet_operator_intent_results WHERE intent_id=%s",
                (intent["intent_id"],),
            ).fetchone()
            if completed is not None:
                return None
            kind = str(intent["intent_kind"])
            if kind == "ACTIVATE":
                return self._activate(connection, intent, authority_at)
            if kind == "DEACTIVATE":
                return self._deactivate(connection, intent, authority_at)
            if kind in {"APPROVE", "REJECT"}:
                return self._approval(connection, intent, authority_at)
            if kind == "REVOKE":
                return self._revoke(connection, intent, authority_at)
            if kind == "CONFIRM_RESET":
                return self._confirm_reset(connection, intent, authority_at)
            if kind == "CANCEL":
                return self._cancel(connection, intent, authority_at)
            return self._complete(
                connection, intent, "BLOCKED", "INTENT_KIND_INVALID", authority_at
            )

    def _complete(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        status: str,
        outcome: str,
        now: datetime,
        **details: object,
    ) -> dict[str, object]:
        result = {"outcome": outcome, **details}
        result_id = canonical_digest(
            ["woozoo.testnet-intent-result/v1", str(intent["intent_id"]), status, outcome]
        )
        connection.execute(
            "INSERT INTO testnet_operator_intent_results"
            "(intent_id,result_id,status,result,completed_at) VALUES (%s,%s,%s,%s,%s)",
            (intent["intent_id"], result_id, status, Jsonb(result), now),
        )
        event_payload = {
            "schema_version": "woozoo.testnet-domain-event/v1",
            "intent_id": intent["intent_id"],
            "intent_kind": intent["intent_kind"],
            "status": status,
            "outcome": outcome,
            **details,
        }
        payload_hash = canonical_digest(cast(CanonicalValue, event_payload))
        event_id = canonical_digest(
            ["woozoo.testnet-domain-event/v1", str(intent["intent_id"]), result_id]
        )
        connection.execute(
            "INSERT INTO testnet_domain_events"
            "(event_id,aggregate_id,event_type,payload_hash,payload,occurred_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                event_id,
                intent["target_id"] or intent["intent_id"],
                f"testnet.{str(intent['intent_kind']).lower()}.processed.v1",
                payload_hash,
                Jsonb(event_payload),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO testnet_outbox(event_id,topic,payload_hash,payload,created_at) "
            "VALUES (%s,'testnet-domain-v1',%s,%s,%s)",
            (event_id, payload_hash, Jsonb(event_payload), now),
        )
        return result

    def _activation_authority(
        self, connection: Connection[dict[str, object]]
    ) -> dict[str, object] | None:
        return connection.execute(
            "SELECT state.*,generation.generation_id FROM testnet_operator_state_v1 state "
            "LEFT JOIN testnet_account_generations generation "
            "ON generation.account_binding_id=state.account_binding_id "
            "AND generation.generation_number=state.generation_number"
        ).fetchone()

    def _activate(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        state = self._activation_authority(connection)
        paper_kill = connection.execute(
            "SELECT active FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone()
        if (
            state is None
            or state["generation_status"] not in {"PENDING_RECONCILIATION", "ACTIVE"}
            or paper_kill is None
            or paper_kill["active"] is not False
            or state["activation_status"] == "ACTIVE"
            or (
                state["generation_status"] == "ACTIVE"
                and state["reconciliation_status"] != "HEALTHY"
            )
        ):
            return self._complete(connection, intent, "BLOCKED", "ACTIVATION_GUARD_FAILED", now)
        activation_id = canonical_digest(
            ["woozoo.testnet-activation/v1", str(intent["intent_id"]), self._binding.build_digest]
        )
        payload_hash = canonical_digest(
            [activation_id, str(intent["request_digest"]), self._binding.configuration_digest]
        )
        request = cast(dict[str, object], intent["request"])
        reason = str(cast(dict[str, object], request["payload"])["reason"])
        connection.execute(
            "INSERT INTO testnet_activations(activation_id,account_binding_id,generation_id,"
            "gateway_instance_id,build_digest,configuration_digest,allowlist_digest,actor_id,"
            "session_digest,csrf_token_digest,origin_hash,status,reason,activated_at,expires_at,"
            "version,payload_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,%s,%s,%s)",
            (
                activation_id,
                state["account_binding_id"],
                state["generation_id"],
                self._binding.gateway_instance_id,
                self._binding.build_digest,
                self._binding.configuration_digest,
                self._binding.allowlist_digest,
                intent["actor_id"],
                intent["session_digest"],
                intent["csrf_token_digest"],
                intent["origin_hash"],
                reason,
                now,
                now + timedelta(minutes=30),
                _integer(intent["expected_version"]) + 1,
                payload_hash,
            ),
        )
        preflight = state["generation_status"] == "PENDING_RECONCILIATION"
        if not preflight:
            connection.execute(
                "UPDATE testnet_safety_state SET active=false,version=version+1,updated_at=%s "
                "WHERE scope='testnet-global' AND active AND reason_code='DEFAULT_OFF'",
                (now,),
            )
        return self._complete(
            connection,
            intent,
            "APPLIED",
            "ACTIVATION_PREFLIGHT_STARTED" if preflight else "ACTIVATED",
            now,
            activation_id=activation_id,
        )

    def _deactivate(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        activation_id = str(intent["target_id"])
        activation = connection.execute(
            "SELECT activation_id FROM testnet_activations WHERE activation_id=%s FOR UPDATE",
            (activation_id,),
        ).fetchone()
        if activation is None:
            return self._complete(connection, intent, "BLOCKED", "ACTIVATION_NOT_FOUND", now)
        revocation_id = canonical_digest(
            ["woozoo.testnet-activation-revocation/v1", str(intent["intent_id"])]
        )
        request = cast(dict[str, object], intent["request"])
        reason = str(cast(dict[str, object], request["payload"])["reason"])
        payload_hash = canonical_digest([revocation_id, activation_id, reason])
        connection.execute(
            "INSERT INTO testnet_activation_revocations(revocation_id,activation_id,actor_id,"
            "session_digest,csrf_token_digest,origin_hash,reason,revoked_at,payload_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                revocation_id,
                activation_id,
                intent["actor_id"],
                intent["session_digest"],
                intent["csrf_token_digest"],
                intent["origin_hash"],
                reason,
                now,
                payload_hash,
            ),
        )
        connection.execute(
            "UPDATE testnet_safety_state SET active=true,version=version+1,"
            "reason_code='MANUAL_STOP',updated_at=%s WHERE scope='testnet-global'",
            (now,),
        )
        return self._complete(connection, intent, "APPLIED", "DEACTIVATED", now)

    def _approval(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        proposal_id = str(intent["target_id"])
        row = connection.execute(
            "SELECT view.*,generation.generation_id FROM testnet_approval_view_v1 view "
            "JOIN testnet_account_generations generation "
            "ON generation.account_binding_id=view.account_binding_id "
            "AND generation.generation_number=view.generation_number "
            "WHERE view.proposal_id=%s ORDER BY preview_created_at DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        state = self._activation_authority(connection)
        paper_kill = connection.execute(
            "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
        ).fetchone()
        if row is None or state is None or paper_kill is None:
            return self._complete(connection, intent, "BLOCKED", "APPROVAL_AUTHORITY_MISSING", now)
        request = cast(dict[str, object], intent["request"])
        payload = cast(dict[str, object], request["payload"])
        preview = cast(dict[str, object], row["preview"])
        preview_material = {
            key: value for key, value in preview.items() if key != "testnet_order_preview_digest"
        }
        risk_input = cast(dict[str, object], row["risk_input"])
        decision_material: dict[str, object] = {
            "schema_version": "woozoo.testnet-risk-decision/v1",
            "decision_id": row["decision_id"],
            "risk_input_digest": row["risk_input_digest"],
            "proposal_id": row["proposal_id"],
            "preview_digest": row["preview_digest"],
            "generation_id": row["generation_id"],
            "verdict": row["verdict"],
            "policy_version": row["policy_version"],
            "ordered_reason_codes": list(cast(list[object], row["ordered_reason_codes"] or [])),
            "decided_at": _timestamp(row["decision_decided_at"]).isoformat().replace("+00:00", "Z"),
            "expires_at": _timestamp(row["decision_expires_at"]).isoformat().replace("+00:00", "Z"),
        }
        latest_source = connection.execute(
            "SELECT decision_id,decision_hash,risk_input_digest,risk_input,verdict,"
            "ordered_reason_codes FROM risk_decisions "
            "WHERE proposal_hash=%s ORDER BY recorded_at DESC,decision_id DESC LIMIT 1",
            (row["proposal_hash"],),
        ).fetchone()
        session = connection.execute(
            "SELECT session.session_digest FROM operator_sessions session "
            "JOIN session_csrf_tokens csrf USING(session_digest) "
            "WHERE session.session_digest=%s AND session.actor_id=%s "
            "AND session.revoked_at IS NULL AND session.idle_expires_at>%s "
            "AND session.absolute_expires_at>%s AND csrf.csrf_token_digest=%s "
            "AND csrf.consumed_at IS NOT NULL AND csrf.expires_at>%s",
            (
                intent["session_digest"],
                intent["actor_id"],
                now,
                now,
                intent["csrf_token_digest"],
                now,
            ),
        ).fetchone()
        source_decision_material = (
            {
                "decision_schema_version": "woozoo.risk-decision/v1",
                "risk_input_digest": latest_source["risk_input_digest"],
                "verdict": latest_source["verdict"],
                "ordered_reason_codes": list(
                    cast(list[object], latest_source["ordered_reason_codes"] or [])
                ),
            }
            if latest_source is not None
            else None
        )
        stored_digests_valid = (
            preview.get("testnet_order_preview_digest") == row["preview_digest"]
            and canonical_digest(cast(CanonicalValue, preview_material)) == row["preview_digest"]
            and canonical_digest(cast(CanonicalValue, risk_input)) == row["risk_input_digest"]
            and canonical_digest(cast(CanonicalValue, decision_material)) == row["decision_hash"]
            and risk_input.get("proposal_hash") == row["proposal_hash"]
            and risk_input.get("account_binding_id") == row["account_binding_id"]
            and risk_input.get("account_generation") == row["generation_number"]
            and risk_input.get("reconciliation_checkpoint_digest") == state["checkpoint_digest"]
            and risk_input.get("testnet_barrier_version") == state["barrier_version"]
            and risk_input.get("paper_kill_version") == paper_kill["version"]
            and latest_source is not None
            and canonical_digest(cast(CanonicalValue, latest_source["risk_input"]))
            == latest_source["risk_input_digest"]
            and risk_input.get("source_risk_decision_id") == latest_source["decision_id"]
            and risk_input.get("source_risk_decision_hash") == latest_source["decision_hash"]
            and source_decision_material is not None
            and canonical_digest(cast(CanonicalValue, source_decision_material))
            == latest_source["decision_hash"]
        )
        checkpoint_created_at = _timestamp(state["checkpoint_created_at"])
        checkpoint_watermark_at = _timestamp(state["checkpoint_watermark_at"])
        view_authority: dict[str, object] = {
            "schema_version": "woozoo.testnet-approval-view-authority/v1",
            "environment": row["environment"],
            "account_binding_id": state["account_binding_id"],
            "generation_id": state["generation_id"],
            "account_generation": state["generation_number"],
            "generation_version": state["generation_version"],
            "proposal_id": row["proposal_id"],
            "proposal_hash": row["proposal_hash"],
            "risk_decision_id": row["decision_id"],
            "risk_decision_hash": row["decision_hash"],
            "risk_input_digest": row["risk_input_digest"],
            "testnet_order_preview_digest": row["preview_digest"],
            "activation_id": state["activation_id"],
            "activation_version": state["activation_version"],
            "activation_status": state["activation_status"],
            "activation_expires_at": _timestamp(state["activation_expires_at"])
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "gateway_instance_id": state["gateway_instance_id"],
            "build_digest": state["build_digest"],
            "configuration_digest": state["configuration_digest"],
            "allowlist_digest": state["allowlist_digest"],
            "paper_kill_active": paper_kill["active"],
            "paper_kill_version": paper_kill["version"],
            "testnet_barrier_active": state["testnet_barrier_active"],
            "testnet_barrier_version": state["barrier_version"],
            "reconciliation_checkpoint_id": state["checkpoint_id"],
            "reconciliation_checkpoint_digest": state["checkpoint_digest"],
            "reconciliation_version": state["checkpoint_version"],
            "reconciliation_status": state["reconciliation_status"],
            "checkpoint_created_at": checkpoint_created_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "checkpoint_watermark_at": checkpoint_watermark_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "ledger_version": state["ledger_version"],
            "ledger_snapshot_digest": risk_input.get("ledger_snapshot_digest"),
        }
        approval_input_digest = canonical_digest(cast(CanonicalValue, view_authority))
        authority_binding: dict[str, object] = {
            "schema_version": "woozoo.testnet-approval-authority/v1",
            "actor_id": intent["actor_id"],
            "session_digest": intent["session_digest"],
            "csrf_token_digest": intent["csrf_token_digest"],
            "origin_hash": intent["origin_hash"],
            "view_authority_digest": approval_input_digest,
            "view_authority": view_authority,
        }
        authority_binding_digest = canonical_digest(cast(CanonicalValue, authority_binding))
        guards = (
            payload.get("testnet_order_preview_digest") == row["preview_digest"]
            and payload.get("approval_input_digest") == approval_input_digest
            and _integer(intent["expected_version"]) == _integer(row["generation_version"])
            and row["verdict"] == "ALLOWED"
            and _timestamp(row["preview_expires_at"]) > now
            and _timestamp(row["decision_expires_at"]) > now
            and row["approval_id"] is None
            and stored_digests_valid
            and session is not None
            and state["generation_id"] == row["generation_id"]
            and checkpoint_watermark_at <= now
            and checkpoint_created_at <= now
            and now - checkpoint_created_at <= timedelta(minutes=5)
            and now - checkpoint_watermark_at <= timedelta(minutes=5)
        )
        if intent["intent_kind"] == "APPROVE":
            guards = guards and (
                state["activation_status"] == "ACTIVE"
                and state["generation_status"] == "ACTIVE"
                and _timestamp(state["activation_expires_at"]) > now
                and state["testnet_barrier_active"] is False
                and state["reconciliation_status"] == "HEALTHY"
                and paper_kill["active"] is False
                and state["configuration_digest"] == self._binding.configuration_digest
                and state["allowlist_digest"] == self._binding.allowlist_digest
                and state["gateway_instance_id"] == self._binding.gateway_instance_id
                and state["build_digest"] == self._binding.build_digest
            )
        if not guards:
            return self._complete(connection, intent, "BLOCKED", "APPROVAL_GUARD_FAILED", now)
        decision = "APPROVED" if intent["intent_kind"] == "APPROVE" else "REJECTED"
        approval_id = canonical_digest(
            ["woozoo.testnet-approval/v1", str(intent["intent_id"]), approval_input_digest]
        )
        approval_nonce = canonical_digest(["woozoo.testnet-approval-nonce/v1", approval_id])
        expires_at = min(
            now + timedelta(minutes=5),
            _timestamp(row["preview_expires_at"]),
            _timestamp(row["decision_expires_at"]),
        )
        approval_hash = canonical_digest(
            cast(
                CanonicalValue,
                {
                    "approval_id": approval_id,
                    "approval_input_digest": approval_input_digest,
                    "decision": decision,
                    "approval_nonce": approval_nonce,
                    "actor_id": intent["actor_id"],
                    "session_digest": intent["session_digest"],
                    "csrf_token_digest": intent["csrf_token_digest"],
                    "origin_hash": intent["origin_hash"],
                    "approved_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                },
            )
        )
        connection.execute(
            "INSERT INTO testnet_approvals(approval_id,proposal_id,proposal_hash,decision_id,"
            "decision_hash,preview_digest,generation_id,actor_id,session_digest,csrf_token_digest,"
            "origin_hash,decision,validity,approval_nonce,approval_input_digest,approved_at,"
            "authority_binding_digest,authority_binding,expires_at,payload_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',"
            "%s,%s,%s,%s,%s,%s,%s)",
            (
                approval_id,
                proposal_id,
                row["proposal_hash"],
                row["decision_id"],
                row["decision_hash"],
                row["preview_digest"],
                row["generation_id"],
                intent["actor_id"],
                intent["session_digest"],
                intent["csrf_token_digest"],
                intent["origin_hash"],
                decision,
                approval_nonce,
                approval_input_digest,
                now,
                authority_binding_digest,
                Jsonb(authority_binding),
                expires_at,
                approval_hash,
            ),
        )
        if decision == "REJECTED":
            return self._complete(
                connection, intent, "APPLIED", "REJECTED", now, approval_id=approval_id
            )
        authorization_id = canonical_digest(
            ["woozoo.testnet-authorization/v1", approval_hash, approval_nonce]
        )
        authorization_nonce = canonical_digest(
            ["woozoo.testnet-authorization-nonce/v1", authorization_id]
        )
        authorization_binding: dict[str, object] = {
            **authority_binding,
            "schema_version": "woozoo.testnet-execution-authorization-authority/v1",
            "approval_id": approval_id,
            "approval_hash": approval_hash,
            "approval_nonce": approval_nonce,
            "authorization_id": authorization_id,
            "authorization_nonce": authorization_nonce,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "revocation_state": "NOT_REVOKED",
        }
        authorization_binding_digest = canonical_digest(cast(CanonicalValue, authorization_binding))
        authorization_input_digest = canonical_digest(
            [
                "woozoo.testnet-execution-authorization-input/v1",
                authorization_binding_digest,
                approval_input_digest,
            ]
        )
        client_order_id = str(preview["client_order_id"])
        command_id = canonical_digest(
            ["woozoo.testnet-order-command/v1", authorization_input_digest, client_order_id]
        )
        command_request_digest = canonical_digest(
            [
                "woozoo.testnet-gateway-request/v1",
                command_id,
                authorization_input_digest,
                str(row["preview_digest"]),
            ]
        )
        command_expires_at = expires_at
        command_payload = {
            "schema_version": "woozoo.testnet-gateway-command/v1",
            "command_id": command_id,
            "command_type": "SUBMIT_LIMIT_ORDER",
            "effect_class": "CREATE_ORDER",
            "producer": "testnet-execution-service",
            "environment": "BINANCE_SPOT_TESTNET",
            "account_binding_id": row["account_binding_id"],
            "account_generation": row["generation_number"],
            "idempotency_key": f"submit:{authorization_id}",
            "request_digest": command_request_digest,
            "correlation_id": proposal_id,
            "causation_id": approval_id,
            "capability_id": "SPOT_TESTNET_SUBMIT_LIMIT_GTC",
            "gateway_allowlist_digest": self._binding.allowlist_digest,
            "gateway_configuration_digest": self._binding.configuration_digest,
            "activation_version": state["activation_version"],
            "testnet_barrier_version": state["barrier_version"],
            "original_authorization_id": authorization_id,
            "original_approval_id": approval_id,
            "original_proposal_hash": row["proposal_hash"],
            "original_risk_decision_hash": row["decision_hash"],
            "client_order_id": client_order_id,
            "symbol": preview["symbol"],
            "side": preview["side"],
            "quantity": preview["quantity"],
            "limit_price": preview["limit_price"],
            "query_reason": "INITIAL_SUBMIT",
            "issued_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": command_expires_at.isoformat().replace("+00:00", "Z"),
        }
        connection.execute(
            "INSERT INTO testnet_execution_authorizations(authorization_id,approval_id,"
            "approval_hash,authorization_nonce,authorization_input_digest,proposal_hash,"
            "authority_binding_digest,authority_binding,decision_hash,preview_digest,generation_id,"
            "client_order_id,issued_at,expires_at,consumed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                authorization_id,
                approval_id,
                approval_hash,
                authorization_nonce,
                authorization_input_digest,
                row["proposal_hash"],
                authorization_binding_digest,
                Jsonb(authorization_binding),
                row["decision_hash"],
                row["preview_digest"],
                row["generation_id"],
                client_order_id,
                now,
                expires_at,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO testnet_gateway_commands(command_id,authorization_id,approval_id,"
            "generation_id,client_order_id,command_type,effect_class,capability_id,"
            "idempotency_key,request_digest,command,issued_at,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,'SUBMIT_LIMIT_ORDER','CREATE_ORDER',%s,%s,%s,%s,%s,%s)",
            (
                command_id,
                authorization_id,
                approval_id,
                row["generation_id"],
                client_order_id,
                "SPOT_TESTNET_SUBMIT_LIMIT_GTC",
                f"submit:{authorization_id}",
                command_request_digest,
                Jsonb(command_payload),
                now,
                command_expires_at,
            ),
        )
        connection.execute(
            "INSERT INTO testnet_orders(order_id,command_id,generation_id,client_order_id,symbol,"
            "side,quantity,limit_price,filled_quantity,status,external_outcome,version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,'PENDING_SUBMIT','QUEUED',1)",
            (
                canonical_digest(["woozoo.testnet-order/v1", command_id]),
                command_id,
                row["generation_id"],
                client_order_id,
                preview["symbol"],
                preview["side"],
                preview["quantity"],
                preview["limit_price"],
            ),
        )
        return self._complete(
            connection,
            intent,
            "APPLIED",
            "AUTHORIZATION_ISSUED",
            now,
            approval_id=approval_id,
            authorization_id=authorization_id,
            execution_id=command_id,
        )

    def _revoke(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        approval_id = str(intent["target_id"])
        approval = connection.execute(
            "SELECT approval.approval_id,auth.consumed_at "
            "FROM testnet_approvals approval LEFT JOIN testnet_execution_authorizations auth "
            "USING(approval_id) WHERE approval.approval_id=%s",
            (approval_id,),
        ).fetchone()
        if approval is None or approval["consumed_at"] is not None:
            return self._complete(connection, intent, "BLOCKED", "REVOCATION_TOO_LATE", now)
        revocation_id = canonical_digest(
            ["woozoo.testnet-approval-revocation/v1", str(intent["intent_id"])]
        )
        request = cast(dict[str, object], intent["request"])
        reason = str(cast(dict[str, object], request["payload"])["reason"])
        payload_hash = canonical_digest([revocation_id, approval_id, reason])
        connection.execute(
            "INSERT INTO testnet_approval_revocations(revocation_id,approval_id,actor_id,"
            "session_digest,csrf_token_digest,origin_hash,reason,revoked_at,payload_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                revocation_id,
                approval_id,
                intent["actor_id"],
                intent["session_digest"],
                intent["csrf_token_digest"],
                intent["origin_hash"],
                reason,
                now,
                payload_hash,
            ),
        )
        return self._complete(connection, intent, "APPLIED", "REVOKED", now)

    def _cancel(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        order = connection.execute(
            "SELECT order_row.*,command.command AS original_command,command.approval_id "
            "FROM testnet_orders order_row JOIN testnet_gateway_commands command USING(command_id) "
            "WHERE order_row.order_id=%s FOR UPDATE OF order_row",
            (intent["target_id"],),
        ).fetchone()
        state = self._activation_authority(connection)
        session = connection.execute(
            "SELECT session.session_digest FROM operator_sessions session "
            "JOIN session_csrf_tokens csrf USING(session_digest) "
            "WHERE session.session_digest=%s AND session.actor_id=%s "
            "AND session.revoked_at IS NULL AND session.idle_expires_at>%s "
            "AND session.absolute_expires_at>%s AND csrf.csrf_token_digest=%s "
            "AND csrf.consumed_at IS NOT NULL AND csrf.expires_at>%s",
            (
                intent["session_digest"],
                intent["actor_id"],
                now,
                now,
                intent["csrf_token_digest"],
                now,
            ),
        ).fetchone()
        if order is None or state is None:
            return self._complete(connection, intent, "BLOCKED", "CANCEL_AUTHORITY_MISSING", now)
        order_material: dict[str, object] = {
            "schema_version": "woozoo.testnet-cancel-order-state/v1",
            "order_id": order["order_id"],
            "command_id": order["command_id"],
            "generation_id": order["generation_id"],
            "client_order_id": order["client_order_id"],
            "exchange_order_id": order["exchange_order_id"],
            "symbol": order["symbol"],
            "side": order["side"],
            "quantity": format(order["quantity"], "f"),
            "limit_price": format(order["limit_price"], "f"),
            "filled_quantity": format(order["filled_quantity"], "f"),
            "status": order["status"],
            "external_outcome": order["external_outcome"],
            "version": order["version"],
        }
        order_digest = canonical_digest(cast(CanonicalValue, order_material))
        request = cast(dict[str, object], intent["request"])
        payload = cast(dict[str, object], request.get("payload", {}))
        checkpoint_created_at = _timestamp(state["checkpoint_created_at"])
        checkpoint_watermark_at = _timestamp(state["checkpoint_watermark_at"])
        guards = (
            session is not None
            and order["generation_id"] == state["generation_id"]
            and _integer(intent["expected_version"]) == _integer(order["version"])
            and payload.get("order_digest") == order_digest
            and order["status"] not in {"FILLED", "CANCELED", "EXPIRED"}
            and state["activation_status"] == "ACTIVE"
            and _timestamp(state["activation_expires_at"]) > now
            and state["configuration_digest"] == self._binding.configuration_digest
            and state["allowlist_digest"] == self._binding.allowlist_digest
            and state["gateway_instance_id"] == self._binding.gateway_instance_id
            and state["build_digest"] == self._binding.build_digest
            and checkpoint_watermark_at <= now
            and checkpoint_created_at <= now
            and now - checkpoint_watermark_at <= timedelta(minutes=5)
            and now - checkpoint_created_at <= timedelta(minutes=5)
        )
        if not guards:
            return self._complete(connection, intent, "BLOCKED", "CANCEL_GUARD_FAILED", now)
        authority_binding: dict[str, object] = {
            "schema_version": "woozoo.testnet-cancel-authorization/v1",
            "order_digest": order_digest,
            "order_version": order["version"],
            "actor_id": intent["actor_id"],
            "session_digest": intent["session_digest"],
            "csrf_token_digest": intent["csrf_token_digest"],
            "origin_hash": intent["origin_hash"],
            "generation_id": state["generation_id"],
            "generation_version": state["generation_version"],
            "activation_id": state["activation_id"],
            "activation_version": state["activation_version"],
            "barrier_active": state["testnet_barrier_active"],
            "barrier_version": state["barrier_version"],
            "paper_kill_active": state["paper_kill_active"],
            "paper_kill_version": state["paper_kill_version"],
            "checkpoint_id": state["checkpoint_id"],
            "checkpoint_digest": state["checkpoint_digest"],
            "checkpoint_version": state["checkpoint_version"],
            "ledger_version": state["ledger_version"],
            "configuration_digest": state["configuration_digest"],
            "allowlist_digest": state["allowlist_digest"],
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "revocation_state": "NOT_REVOKED",
        }
        authorization_input_digest = canonical_digest(cast(CanonicalValue, authority_binding))
        cancel_authorization_id = canonical_digest(
            ["woozoo.testnet-cancel-authorization/v1", authorization_input_digest]
        )
        cancel_nonce = canonical_digest(["woozoo.testnet-cancel-nonce/v1", cancel_authorization_id])
        expires_at = now + timedelta(minutes=5)
        connection.execute(
            "INSERT INTO testnet_cancel_authorizations(cancel_authorization_id,order_id,"
            "generation_id,client_order_id,order_version,order_digest,actor_id,session_digest,"
            "csrf_token_digest,origin_hash,cancel_nonce,authorization_input_digest,"
            "authority_binding,issued_at,expires_at,consumed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                cancel_authorization_id,
                order["order_id"],
                order["generation_id"],
                order["client_order_id"],
                order["version"],
                order_digest,
                intent["actor_id"],
                intent["session_digest"],
                intent["csrf_token_digest"],
                intent["origin_hash"],
                cancel_nonce,
                authorization_input_digest,
                Jsonb(authority_binding),
                now,
                expires_at,
                now,
            ),
        )
        original = dict(cast(dict[str, object], order["original_command"]))
        original["activation_version"] = state["activation_version"]
        original["testnet_barrier_version"] = state["barrier_version"]
        command = build_existing_order_command(
            original,
            command_type="CANCEL_EXISTING_ORDER",
            sequence=1,
            issued_at=now,
            query_reason="OPERATOR_CANCEL",
        )
        command["causation_id"] = cancel_authorization_id
        command["idempotency_key"] = f"cancel:{cancel_authorization_id}"
        command["request_digest"] = canonical_digest(
            [
                "woozoo.testnet-cancel-request/v1",
                str(command["command_id"]),
                cancel_authorization_id,
                order_digest,
                authorization_input_digest,
            ]
        )
        connection.execute(
            "INSERT INTO testnet_gateway_commands(command_id,authorization_id,"
            "cancel_authorization_id,approval_id,generation_id,client_order_id,command_type,"
            "effect_class,capability_id,idempotency_key,request_digest,command,issued_at,expires_at) "
            "VALUES (%s,NULL,%s,%s,%s,%s,'CANCEL_EXISTING_ORDER','REDUCE_OR_CANCEL',"
            "'SPOT_TESTNET_CANCEL_BY_CLIENT_ID',%s,%s,%s,%s,%s)",
            (
                command["command_id"],
                cancel_authorization_id,
                order["approval_id"],
                order["generation_id"],
                order["client_order_id"],
                command["idempotency_key"],
                command["request_digest"],
                Jsonb(command),
                now,
                datetime.fromisoformat(str(command["expires_at"]).replace("Z", "+00:00")),
            ),
        )
        return self._complete(
            connection,
            intent,
            "APPLIED",
            "CANCEL_AUTHORIZATION_ISSUED",
            now,
            cancel_authorization_id=cancel_authorization_id,
            execution_id=command["command_id"],
        )

    def _confirm_reset(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        checkpoint_id = str(intent["target_id"])
        row = connection.execute(
            "SELECT checkpoint.*,generation.account_binding_id,generation.generation_number,"
            "generation.status AS generation_status FROM testnet_reconciliation_checkpoints checkpoint "
            "JOIN testnet_account_generations generation USING(generation_id) "
            "WHERE checkpoint.checkpoint_id=%s",
            (checkpoint_id,),
        ).fetchone()
        request = cast(dict[str, object], intent["request"])
        payload = cast(dict[str, object], request["payload"])
        if (
            row is None
            or row["generation_status"] != "AWAITING_OPERATOR_CONFIRMATION"
            or payload.get("checkpoint_digest") != row["checkpoint_digest"]
        ):
            return self._complete(connection, intent, "BLOCKED", "RESET_CONFIRMATION_INVALID", now)
        next_number = _integer(row["generation_number"]) + 1
        next_generation_id = canonical_digest(
            ["woozoo.testnet-account-generation/v1", str(row["account_binding_id"]), next_number]
        )
        connection.execute(
            "UPDATE testnet_account_generations SET status='RETIRED',closed_at=%s,version=version+1 "
            "WHERE generation_id=%s",
            (now, row["generation_id"]),
        )
        connection.execute(
            "INSERT INTO testnet_account_generations(generation_id,account_binding_id,"
            "generation_number,status,operator_confirmation_digest,opened_at,version) "
            "VALUES (%s,%s,%s,'PENDING_RECONCILIATION',%s,%s,1)",
            (
                next_generation_id,
                row["account_binding_id"],
                next_number,
                canonical_digest([str(intent["intent_id"]), str(row["checkpoint_digest"])]),
                now,
            ),
        )
        return self._complete(
            connection,
            intent,
            "APPLIED",
            "RESET_CONFIRMATION_RECORDED",
            now,
            account_generation=next_number,
        )
