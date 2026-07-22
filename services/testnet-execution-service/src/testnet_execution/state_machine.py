"""IO-free reducer for UNKNOWN outcomes, event replay, and account reset generations."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .canonical import canonical_digest
from .models import (
    AccountGeneration,
    CommandOutcome,
    ObservationRequest,
    OrderObservation,
    OrderState,
    SubmitReceipt,
    TestnetAuthorization,
    TestnetOrder,
)


TERMINAL = frozenset({OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED})
STATUS_RANK = {
    OrderState.PENDING_SUBMIT: 0,
    OrderState.NEW: 1,
    OrderState.PARTIALLY_FILLED: 2,
    OrderState.FILLED: 3,
    OrderState.CANCELED: 3,
    OrderState.EXPIRED: 3,
}


class ExecutionState:
    def __init__(self, generation: AccountGeneration) -> None:
        self.generation = generation
        self.testnet_barrier_active = False
        self.receipts: dict[str, SubmitReceipt] = {}
        self.authorization_nonces: dict[str, str] = {}
        self.outcomes: dict[str, CommandOutcome] = {}
        self.orders: dict[str, TestnetOrder] = {}
        self.observations: dict[str, OrderObservation] = {}
        self.unknown_missing_observations: dict[str, set[str]] = {}
        self.reset_checkpoint_id: str | None = None
        self.create_effect_count = 0
        self.observation_effect_count = 0

    @property
    def new_commands_allowed(self) -> bool:
        return self.generation.status == "ACTIVE" and not self.testnet_barrier_active

    def issue_submit(
        self, authorization: TestnetAuthorization, *, execution_nonce: str, now: datetime
    ) -> SubmitReceipt:
        prior = self.receipts.get(authorization.authorization_id)
        prior_nonce = self.authorization_nonces.get(authorization.authorization_id)
        if prior is not None:
            if prior_nonce != execution_nonce:
                raise ValueError("AUTHORIZATION_ALREADY_CONSUMED")
            return prior
        if not self.new_commands_allowed:
            raise ValueError("TESTNET_COMMANDS_BLOCKED")
        if (
            authorization.revoked
            or now < authorization.issued_at
            or now >= authorization.expires_at
        ):
            raise ValueError("AUTHORIZATION_INVALID")
        if authorization.account_generation != self.generation.number:
            raise ValueError("ACCOUNT_GENERATION_MISMATCH")
        if (
            len(authorization.client_order_id) != 36
            or not authorization.client_order_id.startswith("wz8-")
            or any(
                character not in "0123456789abcdef"
                for character in authorization.client_order_id[4:]
            )
        ):
            raise ValueError("CLIENT_ORDER_ID_INVALID")
        authorization_digest = canonical_digest(
            {
                "authorization_id": authorization.authorization_id,
                "approval_id": authorization.approval_id,
                "proposal_hash": authorization.proposal_hash,
                "risk_decision_hash": authorization.risk_decision_hash,
                "preview_digest": authorization.preview_digest,
                "account_binding_id": authorization.account_binding_id,
                "account_generation": authorization.account_generation,
            }
        )
        nonce_hash = canonical_digest(["execution-nonce/v1", execution_nonce])
        command_id = canonical_digest(
            [
                "woozoo.testnet-order-command/v1",
                authorization_digest,
                nonce_hash,
                authorization.account_generation,
            ]
        )
        receipt = SubmitReceipt(
            command_id,
            authorization.authorization_id,
            authorization.approval_id,
            authorization.client_order_id,
            authorization.account_generation,
            CommandOutcome.QUEUED,
        )
        self.receipts[authorization.authorization_id] = receipt
        self.authorization_nonces[authorization.authorization_id] = execution_nonce
        self.outcomes[command_id] = CommandOutcome.QUEUED
        self.create_effect_count += 1
        self.orders[receipt.client_order_id] = TestnetOrder(
            receipt.client_order_id,
            receipt.account_generation,
            OrderState.PENDING_SUBMIT,
            Decimal(0),
            0,
        )
        return receipt

    def record_dispatch(self, command_id: str) -> None:
        if self.outcomes.get(command_id) is not CommandOutcome.QUEUED:
            raise ValueError("COMMAND_NOT_QUEUED")
        self.outcomes[command_id] = CommandOutcome.DISPATCHED

    def record_outcome(self, command_id: str, outcome: CommandOutcome) -> None:
        current = self.outcomes.get(command_id)
        if current not in {CommandOutcome.DISPATCHED, CommandOutcome.SUBMISSION_UNKNOWN}:
            raise ValueError("COMMAND_OUTCOME_TRANSITION_INVALID")
        if current is CommandOutcome.SUBMISSION_UNKNOWN and outcome is CommandOutcome.DISPATCHED:
            raise ValueError("UNKNOWN_CANNOT_REDISPATCH")
        self.outcomes[command_id] = outcome

    def query_unknown(self, command_id: str) -> ObservationRequest:
        if self.outcomes.get(command_id) is not CommandOutcome.SUBMISSION_UNKNOWN:
            raise ValueError("COMMAND_NOT_UNKNOWN")
        receipt = next(item for item in self.receipts.values() if item.command_id == command_id)
        return ObservationRequest(
            canonical_digest(["unknown-query/v1", command_id, receipt.client_order_id]),
            "QUERY_EXISTING_ORDER",
            receipt.client_order_id,
            receipt.account_generation,
            "UNKNOWN_OUTCOME",
        )

    def record_unknown_query_result(
        self,
        command_id: str,
        *,
        observation_id: str,
        authoritative: bool,
        found: bool,
    ) -> CommandOutcome:
        """Resolve UNKNOWN without any resubmit; absence needs two observations."""

        if self.outcomes.get(command_id) is not CommandOutcome.SUBMISSION_UNKNOWN:
            raise ValueError("COMMAND_NOT_UNKNOWN")
        if not authoritative:
            raise ValueError("OBSERVATION_NOT_AUTHORITATIVE")
        if found:
            self.outcomes[command_id] = CommandOutcome.FOUND
            return CommandOutcome.FOUND
        missing = self.unknown_missing_observations.setdefault(command_id, set())
        missing.add(observation_id)
        if len(missing) >= 2:
            self.outcomes[command_id] = CommandOutcome.NOT_FOUND_CONFIRMED
            return CommandOutcome.NOT_FOUND_CONFIRMED
        return CommandOutcome.SUBMISSION_UNKNOWN

    def apply_order_observation(
        self, observation: OrderObservation, *, original_quantity: str
    ) -> None:
        if observation.account_generation != self.generation.number:
            raise ValueError("OLD_ACCOUNT_GENERATION")
        prior_observation = self.observations.get(observation.observation_id)
        if prior_observation is not None:
            if prior_observation != observation:
                self.testnet_barrier_active = True
                raise ValueError("OBSERVATION_ID_CONFLICT")
            return
        self.observations[observation.observation_id] = observation
        self.observation_effect_count += 1
        try:
            cumulative = Decimal(observation.cumulative_filled_quantity)
            quantity = Decimal(original_quantity)
        except InvalidOperation as error:
            self.testnet_barrier_active = True
            raise ValueError("INVALID_DECIMAL") from error
        if (
            not cumulative.is_finite()
            or not quantity.is_finite()
            or cumulative < 0
            or quantity <= 0
            or cumulative > quantity
        ):
            self.testnet_barrier_active = True
            raise ValueError("OVERFILL")
        try:
            observed_status = OrderState(observation.status)
        except ValueError as error:
            self.testnet_barrier_active = True
            raise ValueError("UNKNOWN_ORDER_STATE") from error
        current = self.orders.get(observation.client_order_id)
        if current is None:
            current = TestnetOrder(
                observation.client_order_id,
                observation.account_generation,
                OrderState.PENDING_SUBMIT,
                Decimal(0),
                0,
            )
        if cumulative < current.filled_quantity:
            return
        if current.status in TERMINAL:
            next_status = current.status
        elif STATUS_RANK[observed_status] < STATUS_RANK[current.status]:
            next_status = current.status
        else:
            next_status = observed_status
        self.orders[observation.client_order_id] = replace(
            current,
            status=next_status,
            filled_quantity=max(current.filled_quantity, cumulative),
            last_source_sequence=max(current.last_source_sequence, observation.source_sequence),
        )

    def suspect_reset(self, checkpoint_id: str) -> None:
        self.testnet_barrier_active = True
        self.reset_checkpoint_id = checkpoint_id
        self.generation = replace(self.generation, status="RESET_SUSPECTED")

    def confirm_reset(self, checkpoint_id: str, *, actor_id: str) -> None:
        if (
            actor_id != "operator-local-1"
            or self.generation.status != "RESET_SUSPECTED"
            or checkpoint_id != self.reset_checkpoint_id
        ):
            raise ValueError("RESET_CONFIRMATION_INVALID")
        self.generation = AccountGeneration(
            self.generation.number + 1,
            "PENDING_RECONCILIATION",
            canonical_digest(["pending-generation/v1", self.generation.number + 1, checkpoint_id]),
        )
        self.testnet_barrier_active = True

    def record_healthy_checkpoint(self, checkpoint_digest: str, *, account_generation: int) -> None:
        if (
            account_generation != self.generation.number
            or self.generation.status != "PENDING_RECONCILIATION"
        ):
            raise ValueError("HEALTHY_CHECKPOINT_GENERATION_MISMATCH")
        self.generation = AccountGeneration(account_generation, "ACTIVE", checkpoint_digest)
        self.testnet_barrier_active = False
