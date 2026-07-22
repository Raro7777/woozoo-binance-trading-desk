"""Deterministic Phase 5 consumer for critical Paper reconciliation results."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from platform_core import canonical_hash

from .kill_switch import KillActivation, KillActivationResult, PostgresKillSwitch


_CRITICAL_REASONS = ("LEDGER_IMBALANCE", "PHYSICAL_LEDGER_MISMATCH")


@dataclass(frozen=True, slots=True)
class ReconciliationKillResult:
    checkpoint_id: str
    activation: KillActivationResult | None
    reason_code: str | None


class PostgresReconciliationKillHandler:
    """Turn an immutable critical checkpoint into one idempotent Kill command."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def handle(self, checkpoint_id: str) -> ReconciliationKillResult:
        with psycopg.connect(self.database_url) as connection:
            checkpoint = connection.execute(
                "SELECT account_id,input_digest,output_digest,status,mismatch_codes,created_at "
                "FROM paper_reconciliation_checkpoints WHERE checkpoint_id=%s",
                (checkpoint_id,),
            ).fetchone()
            if checkpoint is None:
                raise ValueError("RECONCILIATION_CHECKPOINT_MISSING")
            account_id, input_digest, output_digest, status, mismatch_codes, created_at = checkpoint
            if not isinstance(mismatch_codes, list) or not all(
                isinstance(code, str) for code in mismatch_codes
            ):
                raise ValueError("RECONCILIATION_CHECKPOINT_CORRUPT")
            critical = next((code for code in _CRITICAL_REASONS if code in mismatch_codes), None)
            if status != "FAILED" or critical is None:
                return ReconciliationKillResult(checkpoint_id, None, None)

            context_digest = canonical_hash(
                {
                    "account_id": account_id,
                    "checkpoint_id": checkpoint_id,
                    "input_digest": input_digest,
                    "mismatch_codes": mismatch_codes,
                    "output_digest": output_digest,
                }
            )
            request_id = f"reconciliation:{canonical_hash([checkpoint_id, output_digest])}"
            prior = connection.execute(
                "SELECT event.prior_version FROM risk_kill_command_receipts receipt "
                "JOIN kill_switch_events event USING(activation_event_id) "
                "WHERE receipt.request_id=%s",
                (request_id,),
            ).fetchone()
            state = connection.execute(
                "SELECT active,version FROM kill_switch_state WHERE scope='paper-global'"
            ).fetchone()
            if state is None:
                raise RuntimeError("KILL_BARRIER_MISSING")
            if state[0] and prior is None:
                return ReconciliationKillResult(checkpoint_id, None, critical)
            expected_version = prior[0] if prior is not None else state[1]

        command = KillActivation(
            request_id=request_id,
            expected_version=expected_version,
            trigger_kind="INVARIANT",
            actor_id="safety-service:reconciliation",
            reason_code=critical,
            reason=f"critical reconciliation mismatch: {critical}",
            observed_at=created_at,
            context_digest=context_digest,
        )
        try:
            activation = PostgresKillSwitch(self.database_url).activate(command)
        except RuntimeError as error:
            if str(error) != "KILL_SWITCH_ALREADY_ACTIVE":
                raise
            activation = None
        return ReconciliationKillResult(checkpoint_id, activation, critical)
