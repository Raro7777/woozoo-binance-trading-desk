"""Execution-time revalidation for authenticated Testnet operator intents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from psycopg import Connection

from .canonical import CanonicalValue, canonical_digest
from .persistence import ExecutionRuntimeBinding, PostgresIntentWorker, _integer


def activation_view_snapshot(
    row: dict[str, object],
    *,
    now: datetime,
    allowlist_digest: str,
    configuration_digest: str,
) -> dict[str, object]:
    expires_at = row["activation_expires_at"]
    activation_active = (
        row["activation_status"] == "ACTIVE"
        and isinstance(expires_at, datetime)
        and expires_at > now
    )
    generation_status = str(row["generation_status"])
    reconciliation = str(row["reconciliation_status"] or "UNKNOWN")
    paper_kill = row["paper_kill_active"] is not False
    ready = (
        activation_active
        and row["testnet_barrier_active"] is False
        and not paper_kill
        and generation_status == "ACTIVE"
        and reconciliation == "HEALTHY"
    )
    reasons: list[str] = []
    if row["testnet_barrier_active"] is not False:
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
    return {
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
        "capability_allowlist_digest": row["allowlist_digest"] or allowlist_digest,
        "configuration_digest": row["configuration_digest"] or configuration_digest,
        "testnet_barrier_status": (
            "ACTIVE" if row["testnet_barrier_active"] is not False else "INACTIVE"
        ),
        "paper_kill_active": paper_kill,
        "reconciliation_status": reconciliation,
        "reconciliation_checkpoint_id": row["checkpoint_id"],
        "reconciliation_checkpoint_digest": row["checkpoint_digest"],
        "reset_state": (
            "SUSPECTED"
            if generation_status == "RESET_SUSPECTED"
            else "AWAITING_CONFIRMATION"
            if generation_status == "AWAITING_OPERATOR_CONFIRMATION"
            else "NONE"
        ),
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
    }


class ValidatingPostgresIntentWorker(PostgresIntentWorker):
    """Recheck the exact activation version and rendered view in the write transaction."""

    def __init__(self, database_url: str, binding: ExecutionRuntimeBinding) -> None:
        super().__init__(database_url, binding)
        self._validation_binding = binding

    def _activate(
        self,
        connection: Connection[dict[str, object]],
        intent: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        row = connection.execute("SELECT * FROM testnet_operator_state_v1").fetchone()
        request = cast(dict[str, object], intent["request"])
        payload = cast(dict[str, object], request.get("payload", {}))
        if row is None:
            return self._complete(
                connection, intent, "BLOCKED", "ACTIVATION_PRECONDITION_DRIFT", now
            )
        snapshot = activation_view_snapshot(
            dict(row),
            now=now.astimezone(UTC),
            allowlist_digest=self._validation_binding.allowlist_digest,
            configuration_digest=self._validation_binding.configuration_digest,
        )
        snapshot_digest = canonical_digest(cast(CanonicalValue, snapshot))
        if (
            _integer(intent["expected_version"]) != _integer(snapshot["activation_version"])
            or payload.get("activation_view_digest") != snapshot_digest
            or snapshot["activate_action_allowed"] is not True
        ):
            return self._complete(
                connection, intent, "BLOCKED", "ACTIVATION_PRECONDITION_DRIFT", now
            )
        return super()._activate(connection, intent, now)
