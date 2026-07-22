"""Sanitized Testnet operator projections and intent recording for the Control API."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from platform_core.primitives import canonical_hash

from .trading_room import CommandResult, TradingRoomError


ENVIRONMENT = "BINANCE_SPOT_TESTNET"
ACCOUNT_BINDING_ID = canonical_hash(["woozoo.testnet-account/v1", "local-testnet-account"])
ALLOWLIST_DIGEST = canonical_hash(
    ["woozoo.spot-testnet-allowlist/v1", "a5e0bc3ddc0fd7e6bb696849323b74423fa3a54d"]
)
CONFIGURATION_DIGEST = canonical_hash(["woozoo.spot-testnet-gateway-config/v1", "default-off"])
PROPOSAL_ID = "a" * 64


class TestnetOperatorRoom(Protocol):
    def operator_state(self) -> dict[str, object]: ...
    def activate(
        self,
        expected_version: int,
        activation_view_digest: str,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult: ...
    def deactivate(
        self,
        activation_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult: ...
    def approval_view(self, proposal_id: str) -> dict[str, object]: ...
    def decide_approval(
        self,
        *,
        proposal_id: str,
        decision: Literal["APPROVE", "REJECT"],
        expected_version: int,
        preview_digest: str,
        approval_input_digest: str,
        reason: str,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult: ...
    def revoke(
        self,
        approval_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult: ...
    def execution(self, execution_id: str) -> dict[str, object]: ...
    def cancel_view(self, order_id: str) -> dict[str, object]: ...
    def cancel(
        self,
        order_id: str,
        expected_version: int,
        order_digest: str,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult: ...
    def confirm_reset(
        self,
        checkpoint_id: str,
        checkpoint_digest: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult: ...


class InMemoryTestnetOperatorRoom:
    """Deterministic browser-boundary oracle. It has no Gateway import or network capability."""

    def __init__(
        self,
        *,
        ready: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime(2026, 7, 22, tzinfo=UTC))
        self._ready = ready
        self._activation_version = 1 if ready else 0
        self._activation_id = canonical_hash(["testnet-activation/v1", "ready"]) if ready else None
        self._receipts: dict[tuple[str, str], tuple[str, CommandResult]] = {}
        self._approvals: dict[str, dict[str, object]] = {}
        self._executions: dict[str, dict[str, object]] = {}
        self.intent_count = 0
        self.gateway_call_count = 0

    def _now(self) -> datetime:
        return self._clock()

    def _state_digest(self) -> str:
        return canonical_hash(
            {
                "environment": ENVIRONMENT,
                "account_binding_id": ACCOUNT_BINDING_ID,
                "account_generation": 1,
                "activation_id": self._activation_id,
                "activation_version": self._activation_version,
                "ready": self._ready,
                "allowlist_digest": ALLOWLIST_DIGEST,
                "configuration_digest": CONFIGURATION_DIGEST,
            }
        )

    def operator_state(self) -> dict[str, object]:
        ready = self._ready
        return {
            "environment": ENVIRONMENT,
            "account_binding_id": ACCOUNT_BINDING_ID,
            "account_label": "로컬 Spot Testnet 계정",
            "account_generation": 1,
            "activation_id": self._activation_id,
            "activation_status": "ACTIVE" if ready else "DISABLED",
            "activation_version": self._activation_version,
            "activation_view_digest": self._state_digest(),
            "gateway_health": "READY" if ready else "DISABLED",
            "capability_allowlist_digest": ALLOWLIST_DIGEST,
            "configuration_digest": CONFIGURATION_DIGEST,
            "testnet_barrier_status": "INACTIVE" if ready else "ACTIVE",
            "paper_kill_active": False,
            "reconciliation_status": "HEALTHY" if ready else "UNKNOWN",
            "reconciliation_checkpoint_id": canonical_hash(["checkpoint/v1", 1]) if ready else None,
            "reconciliation_checkpoint_digest": (
                canonical_hash(["checkpoint-digest/v1", 1]) if ready else None
            ),
            "reset_state": "NONE",
            "new_commands_allowed": ready,
            "reason_codes": [] if ready else ["TESTNET_DEFAULT_OFF"],
            "activate_action_allowed": not ready,
            "deactivate_action_allowed": ready,
            "reset_confirmation_allowed": False,
            "served_at": self._now().isoformat().replace("+00:00", "Z"),
        }

    def _idempotent(self, scope: str, key: str, request_digest: str) -> CommandResult | None:
        prior = self._receipts.get((scope, key))
        if prior is None:
            return None
        if prior[0] != request_digest:
            raise TradingRoomError("IDEMPOTENCY_CONFLICT", "idempotency key body conflict")
        return CommandResult(200, deepcopy(prior[1].body))

    def _store(
        self, scope: str, key: str, request_digest: str, result: CommandResult
    ) -> CommandResult:
        self._receipts[(scope, key)] = (request_digest, deepcopy(result))
        self.intent_count += 1
        return result

    def activate(
        self,
        expected_version: int,
        activation_view_digest: str,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        body = {
            "expected_version": expected_version,
            "activation_view_digest": activation_view_digest,
            "reason": reason,
        }
        request_digest = canonical_hash(body)
        prior = self._idempotent("activate", idempotency_key, request_digest)
        if prior is not None:
            return prior
        if (
            expected_version != self._activation_version
            or activation_view_digest != self._state_digest()
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet activation view changed", 412)
        intent_id = canonical_hash(
            ["testnet-activation-intent/v1", idempotency_key, request_digest]
        )
        return self._store(
            "activate",
            idempotency_key,
            request_digest,
            CommandResult(
                201,
                {
                    "intent_id": intent_id,
                    "result": "ACTIVATION_PENDING",
                    "resource_version": expected_version + 1,
                },
            ),
        )

    def deactivate(
        self,
        activation_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_digest = canonical_hash([activation_id, expected_version, reason])
        prior = self._idempotent("deactivate", idempotency_key, request_digest)
        if prior is not None:
            return prior
        if activation_id != self._activation_id or expected_version != self._activation_version:
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet activation changed", 412)
        self._ready = False
        self._activation_id = None
        self._activation_version += 1
        intent_id = canonical_hash(["testnet-deactivation-intent/v1", request_digest])
        return self._store(
            "deactivate",
            idempotency_key,
            request_digest,
            CommandResult(
                201,
                {
                    "intent_id": intent_id,
                    "result": "DEACTIVATED",
                    "resource_version": self._activation_version,
                },
            ),
        )

    def _preview(self, proposal_id: str) -> dict[str, object]:
        if proposal_id != PROPOSAL_ID:
            raise TradingRoomError("PROPOSAL_NOT_FOUND", "proposal is unavailable", 404)
        created_at = self._now()
        base: dict[str, object] = {
            "schema_version": "woozoo.testnet-order-preview/v1",
            "environment": ENVIRONMENT,
            "account_binding_id": ACCOUNT_BINDING_ID,
            "account_generation": 1,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_id,
            "evidence_id": "b" * 64,
            "evidence_digest": "c" * 64,
            "data_state_digest": "d" * 64,
            "as_of": created_at.isoformat().replace("+00:00", "Z"),
            "knowledge_cutoff": created_at.isoformat().replace("+00:00", "Z"),
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "quantity": "0.001000000000000000",
            "limit_price": "60000.000000000000000000",
            "worst_case_notional": "60.000000000000000000",
            "fee_reserve": "0.060000000000000000",
            "preview_policy_version": "woozoo.testnet-order-preview-policy/v1",
            "calculator_version": "woozoo.decimal-calculator/v1",
            "symbol_rules_digest": "e" * 64,
            "ledger_snapshot_digest": "f" * 64,
            "reconciliation_checkpoint_digest": canonical_hash(["checkpoint-digest/v1", 1]),
            "paper_kill_version": 0,
            "testnet_barrier_version": 1,
            "client_order_id": "wz8-" + "1" * 32,
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "expires_at": (created_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        }
        base["testnet_order_preview_digest"] = canonical_hash(base)
        return base

    def approval_view(self, proposal_id: str) -> dict[str, object]:
        preview = self._preview(proposal_id)
        preview_digest = str(preview["testnet_order_preview_digest"])
        input_digest = canonical_hash(
            {
                "proposal_id": proposal_id,
                "preview_digest": preview_digest,
                "environment": ENVIRONMENT,
                "account_binding_id": ACCOUNT_BINDING_ID,
                "account_generation": 1,
            }
        )
        approval = self._approvals.get(proposal_id)
        return {
            "environment": ENVIRONMENT,
            "account_binding_id": ACCOUNT_BINDING_ID,
            "account_generation": 1,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_id,
            "risk_decision_id": "2" * 64,
            "risk_decision_hash": "3" * 64,
            "risk_input_digest": "4" * 64,
            "risk_verdict": "ALLOWED",
            "risk_policy_version": "woozoo.testnet-risk-policy/v1",
            "preview_policy_version": "woozoo.testnet-order-preview-policy/v1",
            "testnet_order_preview": preview,
            "testnet_order_preview_digest": preview_digest,
            "approval_input_digest": input_digest,
            "approval_id": approval.get("approval_id") if approval else None,
            "approval_decision": approval.get("approval_decision") if approval else None,
            "approval_validity": approval.get("approval_validity") if approval else None,
            "authorization_id": approval.get("authorization_id") if approval else None,
            "authorization_status": (
                approval.get("authorization_status") if approval else "NOT_ISSUED"
            ),
            "execution_id": approval.get("execution_id") if approval else None,
            "approval_ttl_seconds": 300,
            "view_version": 1,
            "reason_codes": [] if self._ready else ["TESTNET_DEFAULT_OFF"],
            "approve_action_allowed": self._ready and approval is None,
            "reject_action_allowed": approval is None,
            "served_at": self._now().isoformat().replace("+00:00", "Z"),
        }

    def decide_approval(
        self,
        *,
        proposal_id: str,
        decision: Literal["APPROVE", "REJECT"],
        expected_version: int,
        preview_digest: str,
        approval_input_digest: str,
        reason: str,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_digest = canonical_hash(
            [proposal_id, decision, expected_version, preview_digest, approval_input_digest, reason]
        )
        prior = self._idempotent("approval", idempotency_key, request_digest)
        if prior is not None:
            return prior
        view = self.approval_view(proposal_id)
        if (
            expected_version != view["view_version"]
            or preview_digest != view["testnet_order_preview_digest"]
            or approval_input_digest != view["approval_input_digest"]
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet approval view changed", 412)
        if decision == "APPROVE" and not self._ready:
            raise TradingRoomError("TESTNET_DEFAULT_OFF", "Testnet command is disabled", 409)
        approval_id = canonical_hash(["testnet-approval/v1", request_digest])
        authorization_id = (
            canonical_hash(["testnet-authorization/v1", approval_id])
            if decision == "APPROVE"
            else None
        )
        execution_id = (
            canonical_hash(["testnet-execution/v1", authorization_id])
            if authorization_id is not None
            else None
        )
        body: dict[str, object] = {
            "result": "AUTHORIZATION_ISSUED" if decision == "APPROVE" else "REJECTED",
            "approval_id": approval_id,
            "approval_decision": "APPROVED" if decision == "APPROVE" else "REJECTED",
            "approval_validity": "ACTIVE",
            "authorization_status": "ISSUED" if decision == "APPROVE" else "NOT_ISSUED",
            "execution_id": execution_id,
        }
        self._approvals[proposal_id] = deepcopy(body)
        if execution_id is not None:
            order_id = canonical_hash(["testnet-order/v1", execution_id])
            self._executions[execution_id] = {
                "execution_id": execution_id,
                "order_id": order_id,
                "order_version": 1,
                "authorization_id": authorization_id,
                "command_id": canonical_hash(["testnet-command/v1", authorization_id]),
                "client_order_id": "wz8-" + "1" * 32,
                "environment": ENVIRONMENT,
                "account_generation": 1,
                "command_status": "QUEUED",
                "external_outcome": "UNKNOWN",
                "order_status": "PENDING_SUBMIT",
                "reconciliation_status": "HEALTHY",
                "reconciliation_checkpoint_id": canonical_hash(["checkpoint/v1", 1]),
                "reset_state": "NONE",
                "reason_codes": [],
                "submitted_at": None,
                "last_observed_at": None,
            }
        return self._store("approval", idempotency_key, request_digest, CommandResult(201, body))

    def revoke(
        self,
        approval_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_digest = canonical_hash([approval_id, expected_version, reason])
        prior = self._idempotent("revoke", idempotency_key, request_digest)
        if prior is not None:
            return prior
        found = next(
            (item for item in self._approvals.values() if item["approval_id"] == approval_id), None
        )
        if found is None:
            raise TradingRoomError("APPROVAL_NOT_FOUND", "Testnet approval is unavailable", 404)
        found["approval_validity"] = "REVOKED"
        found["authorization_status"] = "REVOKED"
        return self._store(
            "revoke",
            idempotency_key,
            request_digest,
            CommandResult(
                201,
                {
                    "intent_id": canonical_hash(["testnet-revocation/v1", request_digest]),
                    "result": "REVOKED",
                    "resource_version": expected_version + 1,
                },
            ),
        )

    def execution(self, execution_id: str) -> dict[str, object]:
        value = self._executions.get(execution_id)
        if value is None:
            raise TradingRoomError("EXECUTION_NOT_FOUND", "Testnet execution is unavailable", 404)
        return deepcopy(value)

    def cancel_view(self, order_id: str) -> dict[str, object]:
        execution = next(
            (value for value in self._executions.values() if value.get("order_id") == order_id),
            None,
        )
        if execution is None:
            raise TradingRoomError("ORDER_NOT_FOUND", "Testnet order is unavailable", 404)
        material: dict[str, object] = {
            "schema_version": "woozoo.testnet-cancel-order-state/v1",
            "order_id": order_id,
            "command_id": execution["command_id"],
            "generation_id": canonical_hash(["testnet-generation/v1", 1]),
            "client_order_id": execution["client_order_id"],
            "exchange_order_id": "12345",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": "0.001000000000000000",
            "limit_price": "60000.000000000000000000",
            "filled_quantity": "0",
            "status": execution["order_status"],
            "external_outcome": execution["external_outcome"],
            "version": execution["order_version"],
        }
        return {
            "environment": ENVIRONMENT,
            "order": material,
            "order_digest": canonical_hash(material),
            "view_version": execution["order_version"],
            "cancel_action_allowed": execution["order_status"] in {"NEW", "PARTIALLY_FILLED"},
            "cancel_authorization_id": None,
            "cancel_execution_id": None,
            "reason_codes": [],
            "served_at": self._now().isoformat().replace("+00:00", "Z"),
        }

    def cancel(
        self,
        order_id: str,
        expected_version: int,
        order_digest: str,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        view = self.cancel_view(order_id)
        request_digest = canonical_hash([order_id, expected_version, order_digest, reason])
        prior = self._idempotent("cancel", idempotency_key, request_digest)
        if prior is not None:
            return prior
        if (
            expected_version != view["view_version"]
            or order_digest != view["order_digest"]
            or view["cancel_action_allowed"] is not True
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet cancel view changed", 412)
        return self._store(
            "cancel",
            idempotency_key,
            request_digest,
            CommandResult(
                201,
                {
                    "intent_id": canonical_hash(["testnet-cancel-intent/v1", request_digest]),
                    "result": "CANCEL_PENDING",
                    "resource_version": expected_version,
                },
            ),
        )

    def confirm_reset(
        self,
        checkpoint_id: str,
        checkpoint_digest: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_digest = canonical_hash(
            [checkpoint_id, checkpoint_digest, expected_version, reason]
        )
        prior = self._idempotent("reset", idempotency_key, request_digest)
        if prior is not None:
            return prior
        if self.operator_state()["reset_confirmation_allowed"] is not True:
            raise TradingRoomError("RESET_CONFIRMATION_BLOCKED", "reset confirmation is blocked")
        return self._store(
            "reset",
            idempotency_key,
            request_digest,
            CommandResult(
                201,
                {
                    "intent_id": canonical_hash(["testnet-reset-confirmation/v1", request_digest]),
                    "result": "RESET_CONFIRMATION_RECORDED",
                    "resource_version": expected_version + 1,
                },
            ),
        )


class PostgresTestnetOperatorRoom:
    """Durable intent writer over sanitized Postgres projections.

    The Control API can only append authenticated intents. It cannot create a
    Gateway command, consume an authorization, or contact the exchange.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None

    @staticmethod
    def _unavailable_state(reason: str) -> dict[str, object]:
        now = datetime.now(UTC)
        base: dict[str, object] = {
            "environment": ENVIRONMENT,
            "account_binding_id": ACCOUNT_BINDING_ID,
            "account_label": "구성되지 않은 Spot Testnet 계정",
            "account_generation": 1,
            "activation_id": None,
            "activation_status": "DISABLED",
            "activation_version": 0,
            "gateway_health": "DISABLED",
            "capability_allowlist_digest": ALLOWLIST_DIGEST,
            "configuration_digest": CONFIGURATION_DIGEST,
            "testnet_barrier_status": "ACTIVE",
            "paper_kill_active": False,
            "reconciliation_status": "UNKNOWN",
            "reconciliation_checkpoint_id": None,
            "reconciliation_checkpoint_digest": None,
            "reset_state": "NONE",
            "new_commands_allowed": False,
            "reason_codes": [reason],
            "activate_action_allowed": False,
            "deactivate_action_allowed": False,
            "reset_confirmation_allowed": False,
            "served_at": now.isoformat().replace("+00:00", "Z"),
        }
        base["activation_view_digest"] = canonical_hash(base)
        return base

    def operator_state(self) -> dict[str, object]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute("SELECT * FROM testnet_operator_state_v1").fetchone()
        except psycopg.Error as error:
            raise TradingRoomError(
                "TESTNET_PROJECTION_UNAVAILABLE", "Testnet operator projection is unavailable", 503
            ) from error
        if row is None or row["account_binding_id"] is None:
            return self._unavailable_state("TESTNET_ACCOUNT_UNAVAILABLE")
        now = datetime.now(UTC)
        activation_active = (
            row["activation_status"] == "ACTIVE"
            and row["activation_expires_at"] is not None
            and row["activation_expires_at"] > now
        )
        generation_status = str(row["generation_status"])
        reconciliation = str(row["reconciliation_status"] or "UNKNOWN")
        paper_kill = row["paper_kill_active"] is not False
        ready = (
            activation_active
            and not row["testnet_barrier_active"]
            and not paper_kill
            and generation_status == "ACTIVE"
            and reconciliation == "HEALTHY"
        )
        reasons: list[str] = []
        if row["testnet_barrier_active"]:
            reasons.append(str(row["reason_code"]))
        if paper_kill:
            reasons.append("PAPER_KILL_ACTIVE")
        if generation_status != "ACTIVE":
            reasons.append("ACCOUNT_GENERATION_NOT_ACTIVE")
        if reconciliation != "HEALTHY":
            reasons.append("RECONCILIATION_NOT_HEALTHY")
        if not activation_active:
            reasons.append("TESTNET_DEFAULT_OFF")
        elif not ready:
            reasons.append("GATEWAY_ACTIVATING")
        reset_state = (
            "SUSPECTED"
            if generation_status == "RESET_SUSPECTED"
            else "AWAITING_CONFIRMATION"
            if generation_status == "AWAITING_OPERATOR_CONFIRMATION"
            else "NONE"
        )
        value: dict[str, object] = {
            "environment": row["environment"],
            "account_binding_id": row["account_binding_id"],
            "account_label": row["account_label"],
            "account_generation": row["generation_number"],
            "activation_id": row["activation_id"],
            "activation_status": row["activation_status"] or "DISABLED",
            "activation_version": row["activation_version"] or 0,
            "gateway_health": (
                "READY"
                if ready
                else "KILLED"
                if paper_kill
                else "ACTIVATING"
                if activation_active and generation_status == "PENDING_RECONCILIATION"
                else "DEGRADED"
                if activation_active
                else "DISABLED"
            ),
            "capability_allowlist_digest": row["allowlist_digest"] or ALLOWLIST_DIGEST,
            "configuration_digest": row["configuration_digest"] or CONFIGURATION_DIGEST,
            "testnet_barrier_status": "ACTIVE" if row["testnet_barrier_active"] else "INACTIVE",
            "paper_kill_active": paper_kill,
            "reconciliation_status": reconciliation,
            "reconciliation_checkpoint_id": row["checkpoint_id"],
            "reconciliation_checkpoint_digest": row["checkpoint_digest"],
            "reset_state": reset_state,
            "new_commands_allowed": ready,
            "reason_codes": list(dict.fromkeys(reasons)),
            "activate_action_allowed": (
                not activation_active
                and not paper_kill
                and (
                    (generation_status == "PENDING_RECONCILIATION" and reconciliation == "UNKNOWN")
                    or (generation_status == "ACTIVE" and reconciliation == "HEALTHY")
                )
            ),
            "deactivate_action_allowed": activation_active,
            "reset_confirmation_allowed": generation_status == "AWAITING_OPERATOR_CONFIRMATION",
            "served_at": now.isoformat().replace("+00:00", "Z"),
        }
        value["activation_view_digest"] = canonical_hash(
            {key: item for key, item in value.items() if key != "served_at"}
        )
        return value

    def _record_intent(
        self,
        *,
        kind: str,
        target_id: str | None,
        expected_version: int,
        payload: dict[str, object],
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        request = {
            "intent_kind": kind,
            "target_id": target_id,
            "expected_version": expected_version,
            "payload": payload,
        }
        request_digest = canonical_hash(request)
        intent_id = canonical_hash(
            ["woozoo.testnet-operator-intent/v1", idempotency_key, request_digest]
        )
        receipt: dict[str, object] = {
            "intent_id": intent_id,
            "result": f"{kind}_PENDING",
            "resource_version": expected_version,
        }
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                prior = connection.execute(
                    "SELECT request_digest,receipt FROM testnet_operator_intent_receipts_v1 "
                    "WHERE idempotency_key=%s",
                    (idempotency_key,),
                ).fetchone()
                if prior is not None:
                    if prior["request_digest"] != request_digest:
                        raise TradingRoomError(
                            "IDEMPOTENCY_CONFLICT", "idempotency key body conflict"
                        )
                    return CommandResult(200, dict(prior["receipt"]))
                connection.execute(
                    "INSERT INTO testnet_operator_intents("
                    "intent_id,intent_kind,target_id,idempotency_key,request_digest,"
                    "expected_version,actor_id,session_digest,csrf_token_digest,origin_hash,"
                    "request,receipt,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)",
                    (
                        intent_id,
                        kind,
                        target_id,
                        idempotency_key,
                        request_digest,
                        expected_version,
                        "operator-local-1",
                        session_binding_hash,
                        csrf_binding_hash,
                        origin_hash,
                        Jsonb(request),
                        Jsonb(receipt),
                    ),
                )
        except TradingRoomError:
            raise
        except psycopg.Error as error:
            raise TradingRoomError(
                "TESTNET_INTENT_UNAVAILABLE", "Testnet intent authority is unavailable", 503
            ) from error
        return CommandResult(201, receipt)

    def activate(
        self,
        expected_version: int,
        activation_view_digest: str,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        state = self.operator_state()
        if (
            expected_version != state["activation_version"]
            or activation_view_digest != state["activation_view_digest"]
            or state["activate_action_allowed"] is not True
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet activation view changed", 412)
        return self._record_intent(
            kind="ACTIVATE",
            target_id=None,
            expected_version=expected_version,
            payload={"activation_view_digest": activation_view_digest, "reason": reason},
            idempotency_key=idempotency_key,
            session_binding_hash=session_binding_hash,
            csrf_binding_hash=csrf_binding_hash,
            origin_hash=origin_hash,
        )

    def deactivate(
        self,
        activation_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        state = self.operator_state()
        if (
            activation_id != state["activation_id"]
            or expected_version != state["activation_version"]
            or state["deactivate_action_allowed"] is not True
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet activation changed", 412)
        return self._record_intent(
            kind="DEACTIVATE",
            target_id=activation_id,
            expected_version=expected_version,
            payload={"reason": reason},
            idempotency_key=idempotency_key,
            session_binding_hash=session_binding_hash,
            csrf_binding_hash=csrf_binding_hash,
            origin_hash=origin_hash,
        )

    def approval_view(self, proposal_id: str) -> dict[str, object]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    "SELECT approval.*,"
                    "state.generation_id AS authority_generation_id,"
                    "state.generation_version AS authority_generation_version,"
                    "state.activation_id AS authority_activation_id,"
                    "state.activation_version AS authority_activation_version,"
                    "state.activation_status AS authority_activation_status,"
                    "state.activation_expires_at AS authority_activation_expires_at,"
                    "state.gateway_instance_id AS authority_gateway_instance_id,"
                    "state.build_digest AS authority_build_digest,"
                    "state.configuration_digest AS authority_configuration_digest,"
                    "state.allowlist_digest AS authority_allowlist_digest,"
                    "state.paper_kill_active AS authority_paper_kill_active,"
                    "state.paper_kill_version AS authority_paper_kill_version,"
                    "state.testnet_barrier_active AS authority_testnet_barrier_active,"
                    "state.barrier_version AS authority_testnet_barrier_version,"
                    "state.checkpoint_id AS authority_checkpoint_id,"
                    "state.checkpoint_digest AS authority_checkpoint_digest,"
                    "state.checkpoint_version AS authority_reconciliation_version,"
                    "state.reconciliation_status AS authority_reconciliation_status,"
                    "state.checkpoint_created_at AS authority_checkpoint_created_at,"
                    "state.checkpoint_watermark_at AS authority_checkpoint_watermark_at,"
                    "state.ledger_version AS authority_ledger_version "
                    "FROM testnet_approval_view_v1 approval "
                    "JOIN testnet_operator_state_v1 state "
                    "ON state.account_binding_id=approval.account_binding_id "
                    "AND state.generation_number=approval.generation_number "
                    "WHERE approval.proposal_id=%s "
                    "ORDER BY approval.preview_created_at DESC LIMIT 1",
                    (proposal_id,),
                ).fetchone()
        except psycopg.Error as error:
            raise TradingRoomError(
                "TESTNET_PROJECTION_UNAVAILABLE", "Testnet approval projection is unavailable", 503
            ) from error
        if row is None:
            raise TradingRoomError("PROPOSAL_NOT_FOUND", "proposal is unavailable", 404)
        state = self.operator_state()
        now = datetime.now(UTC)
        active_approval = row["approval_id"] is not None
        ready = (
            state["new_commands_allowed"] is True
            and row["verdict"] == "ALLOWED"
            and row["preview_expires_at"] > now
            and row["decision_expires_at"] > now
            and not active_approval
        )
        preview = dict(row["preview"])
        risk_input = dict(row["risk_input"])
        approval_authority: dict[str, object] = {
            "schema_version": "woozoo.testnet-approval-view-authority/v1",
            "environment": row["environment"],
            "account_binding_id": row["account_binding_id"],
            "generation_id": row["authority_generation_id"],
            "account_generation": row["generation_number"],
            "generation_version": row["authority_generation_version"],
            "proposal_id": row["proposal_id"],
            "proposal_hash": row["proposal_hash"],
            "risk_decision_id": row["decision_id"],
            "risk_decision_hash": row["decision_hash"],
            "risk_input_digest": row["risk_input_digest"],
            "testnet_order_preview_digest": row["preview_digest"],
            "activation_id": row["authority_activation_id"],
            "activation_version": row["authority_activation_version"],
            "activation_status": row["authority_activation_status"],
            "activation_expires_at": self._iso(row["authority_activation_expires_at"]),
            "gateway_instance_id": row["authority_gateway_instance_id"],
            "build_digest": row["authority_build_digest"],
            "configuration_digest": row["authority_configuration_digest"],
            "allowlist_digest": row["authority_allowlist_digest"],
            "paper_kill_active": row["authority_paper_kill_active"],
            "paper_kill_version": row["authority_paper_kill_version"],
            "testnet_barrier_active": row["authority_testnet_barrier_active"],
            "testnet_barrier_version": row["authority_testnet_barrier_version"],
            "reconciliation_checkpoint_id": row["authority_checkpoint_id"],
            "reconciliation_checkpoint_digest": row["authority_checkpoint_digest"],
            "reconciliation_version": row["authority_reconciliation_version"],
            "reconciliation_status": row["authority_reconciliation_status"],
            "checkpoint_created_at": self._iso(row["authority_checkpoint_created_at"]),
            "checkpoint_watermark_at": self._iso(row["authority_checkpoint_watermark_at"]),
            "ledger_version": row["authority_ledger_version"],
            "ledger_snapshot_digest": risk_input.get("ledger_snapshot_digest"),
        }
        approval_input_digest = canonical_hash(approval_authority)
        reasons = list(row["ordered_reason_codes"] or [])
        reasons.extend(state["reason_codes"] if isinstance(state["reason_codes"], list) else [])
        authorization_status = (
            "NOT_ISSUED"
            if row["authorization_id"] is None
            else "CONSUMED"
            if row["consumed_at"] is not None
            else "ISSUED"
        )
        return {
            "environment": row["environment"],
            "account_binding_id": row["account_binding_id"],
            "account_generation": row["generation_number"],
            "proposal_id": row["proposal_id"],
            "proposal_hash": row["proposal_hash"],
            "risk_decision_id": row["decision_id"],
            "risk_decision_hash": row["decision_hash"],
            "risk_input_digest": row["risk_input_digest"],
            "risk_verdict": row["verdict"],
            "risk_policy_version": row["policy_version"],
            "preview_policy_version": preview.get("preview_policy_version"),
            "testnet_order_preview": preview,
            "testnet_order_preview_digest": row["preview_digest"],
            "approval_authority": approval_authority,
            "approval_input_digest": approval_input_digest,
            "approval_id": row["approval_id"],
            "approval_decision": row["approval_decision"],
            "approval_validity": row["approval_validity"],
            "authorization_id": row["authorization_id"],
            "authorization_status": authorization_status,
            "execution_id": row["command_id"],
            "approval_ttl_seconds": 300,
            "view_version": int(row["generation_version"]),
            "reason_codes": list(dict.fromkeys(str(reason) for reason in reasons)),
            "approve_action_allowed": ready,
            "reject_action_allowed": not active_approval,
            "served_at": now.isoformat().replace("+00:00", "Z"),
        }

    def decide_approval(
        self,
        *,
        proposal_id: str,
        decision: Literal["APPROVE", "REJECT"],
        expected_version: int,
        preview_digest: str,
        approval_input_digest: str,
        reason: str,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        view = self.approval_view(proposal_id)
        action_allowed = (
            view["approve_action_allowed"]
            if decision == "APPROVE"
            else view["reject_action_allowed"]
        )
        if (
            expected_version != view["view_version"]
            or preview_digest != view["testnet_order_preview_digest"]
            or approval_input_digest != view["approval_input_digest"]
            or action_allowed is not True
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet approval view changed", 412)
        return self._record_intent(
            kind=decision,
            target_id=proposal_id,
            expected_version=expected_version,
            payload={
                "proposal_id": proposal_id,
                "testnet_order_preview_digest": preview_digest,
                "approval_input_digest": approval_input_digest,
                "reason": reason,
            },
            idempotency_key=idempotency_key,
            session_binding_hash=session_binding_hash,
            csrf_binding_hash=csrf_binding_hash,
            origin_hash=origin_hash,
        )

    def revoke(
        self,
        approval_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        return self._record_intent(
            kind="REVOKE",
            target_id=approval_id,
            expected_version=expected_version,
            payload={"reason": reason},
            idempotency_key=idempotency_key,
            session_binding_hash=session_binding_hash,
            csrf_binding_hash=csrf_binding_hash,
            origin_hash=origin_hash,
        )

    def execution(self, execution_id: str) -> dict[str, object]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    "SELECT * FROM testnet_execution_view_v1 WHERE execution_id=%s",
                    (execution_id,),
                ).fetchone()
        except psycopg.Error as error:
            raise TradingRoomError(
                "TESTNET_PROJECTION_UNAVAILABLE", "Testnet execution projection is unavailable", 503
            ) from error
        if row is None:
            raise TradingRoomError("EXECUTION_NOT_FOUND", "Testnet execution is unavailable", 404)
        raw_status = str(row["command_status"])
        command_status = {
            "DISPATCH_RECORDED": "DISPATCHED",
            "SUBMISSION_UNKNOWN": "UNKNOWN_OUTCOME",
            "EXCHANGE_ACKNOWLEDGED": "FOUND",
            "RESOLVED_FOUND": "FOUND",
            "RESOLVED_REJECTED": "REJECTED",
            "RESOLVED_NOT_FOUND_CONFIRMED": "NOT_FOUND_CONFIRMED",
            "REJECTED_LOCAL": "BLOCKED",
        }.get(raw_status, "QUEUED")
        raw_outcome = str(row["external_outcome"])
        external_outcome = (
            raw_outcome
            if raw_outcome in {"FOUND", "REJECTED", "NOT_FOUND_CONFIRMED"}
            else "UNKNOWN"
        )
        generation_status = str(row["reset_state"])
        reset_state = (
            "SUSPECTED"
            if generation_status == "RESET_SUSPECTED"
            else "AWAITING_CONFIRMATION"
            if generation_status == "AWAITING_OPERATOR_CONFIRMATION"
            else "NONE"
        )
        return {
            "execution_id": row["execution_id"],
            "order_id": row["order_id"],
            "order_version": row["order_version"],
            "authorization_id": row["authorization_id"],
            "command_id": row["command_id"],
            "client_order_id": row["client_order_id"],
            "environment": row["environment"],
            "account_generation": row["generation_number"],
            "command_status": command_status,
            "external_outcome": external_outcome,
            "order_status": row["order_status"],
            "reconciliation_status": row["reconciliation_status"] or "UNKNOWN",
            "reconciliation_checkpoint_id": row["checkpoint_id"],
            "reset_state": reset_state,
            "reason_codes": ["SUBMISSION_UNKNOWN"] if command_status == "UNKNOWN_OUTCOME" else [],
            "submitted_at": self._iso(row["submitted_at"]),
            "last_observed_at": self._iso(row["last_observed_at"]),
        }

    @staticmethod
    def _cancel_order_material(row: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": "woozoo.testnet-cancel-order-state/v1",
            "order_id": row["order_id"],
            "command_id": row["command_id"],
            "generation_id": row["generation_id"],
            "client_order_id": row["client_order_id"],
            "exchange_order_id": row["exchange_order_id"],
            "symbol": row["symbol"],
            "side": row["side"],
            "quantity": format(row["quantity"], "f"),
            "limit_price": format(row["limit_price"], "f"),
            "filled_quantity": format(row["filled_quantity"], "f"),
            "status": row["status"],
            "external_outcome": row["external_outcome"],
            "version": row["version"],
        }

    def cancel_view(self, order_id: str) -> dict[str, object]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                raw = connection.execute(
                    "SELECT * FROM testnet_cancel_view_v1 WHERE order_id=%s", (order_id,)
                ).fetchone()
        except psycopg.Error as error:
            raise TradingRoomError(
                "TESTNET_PROJECTION_UNAVAILABLE", "Testnet cancel projection is unavailable", 503
            ) from error
        if raw is None:
            raise TradingRoomError("ORDER_NOT_FOUND", "Testnet order is unavailable", 404)
        row = dict(raw)
        material = self._cancel_order_material(row)
        order_digest = canonical_hash(material)
        terminal = str(row["status"]) in {"FILLED", "CANCELED", "EXPIRED"}
        already_authorized = row["cancel_authorization_id"] is not None
        reasons: list[str] = []
        if terminal:
            reasons.append("ORDER_TERMINAL")
        if already_authorized:
            reasons.append("CANCEL_ALREADY_AUTHORIZED")
        return {
            "environment": row["environment"],
            "account_generation": row["generation_number"],
            "order": material,
            "order_digest": order_digest,
            "view_version": row["version"],
            "cancel_action_allowed": not terminal and not already_authorized,
            "cancel_authorization_id": row["cancel_authorization_id"],
            "cancel_authorization_input_digest": row["cancel_authorization_input_digest"],
            "cancel_execution_id": row["cancel_command_id"],
            "reason_codes": reasons,
            "served_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def cancel(
        self,
        order_id: str,
        expected_version: int,
        order_digest: str,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        view = self.cancel_view(order_id)
        if (
            expected_version != view["view_version"]
            or order_digest != view["order_digest"]
            or view["cancel_action_allowed"] is not True
        ):
            raise TradingRoomError("PRECONDITION_FAILED", "Testnet cancel view changed", 412)
        return self._record_intent(
            kind="CANCEL",
            target_id=order_id,
            expected_version=expected_version,
            payload={"order_digest": order_digest, "reason": reason},
            idempotency_key=idempotency_key,
            session_binding_hash=session_binding_hash,
            csrf_binding_hash=csrf_binding_hash,
            origin_hash=origin_hash,
        )

    def confirm_reset(
        self,
        checkpoint_id: str,
        checkpoint_digest: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str,
        session_binding_hash: str,
        csrf_binding_hash: str,
        origin_hash: str,
    ) -> CommandResult:
        state = self.operator_state()
        if (
            state["reset_confirmation_allowed"] is not True
            or checkpoint_id != state["reconciliation_checkpoint_id"]
            or checkpoint_digest != state["reconciliation_checkpoint_digest"]
        ):
            raise TradingRoomError("RESET_CONFIRMATION_BLOCKED", "reset confirmation is blocked")
        return self._record_intent(
            kind="CONFIRM_RESET",
            target_id=checkpoint_id,
            expected_version=expected_version,
            payload={"checkpoint_digest": checkpoint_digest, "reason": reason},
            idempotency_key=idempotency_key,
            session_binding_hash=session_binding_hash,
            csrf_binding_hash=csrf_binding_hash,
            origin_hash=origin_hash,
        )
