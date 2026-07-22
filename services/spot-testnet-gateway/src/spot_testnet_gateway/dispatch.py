"""Write-ahead dispatch boundary with durable UNKNOWN semantics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActivationAuthority:
    deployment_enabled: bool
    operator_active: bool
    environment: str
    account_generation: int
    gateway_instance_id: str
    build_digest: str
    configuration_digest: str
    allowlist_digest: str
    gateway_health: str
    paper_kill_active: bool
    testnet_barrier_active: bool
    reconciliation_status: str


@dataclass(frozen=True, slots=True)
class GatewayCommand:
    command_id: str
    request_digest: str
    command_type: str
    effect_class: str
    environment: str
    account_generation: int
    client_order_id: str
    configuration_digest: str
    allowlist_digest: str


@dataclass(frozen=True, slots=True)
class GatewayRuntimeBinding:
    """Identity of the running binary; never inferred from a database row."""

    gateway_instance_id: str
    build_digest: str
    configuration_digest: str
    allowlist_digest: str


@dataclass(frozen=True, slots=True)
class TransportResult:
    status: str


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    command_id: str
    request_digest: str
    client_order_id: str
    status: str
    external_effect_count: int


class InMemoryDispatchStore:
    """Test oracle mirroring the Postgres write-ahead transaction boundary."""

    def __init__(self) -> None:
        self.receipts: dict[str, DispatchReceipt] = {}
        self.request_digests: dict[str, str] = {}
        self.dispatch_attempt_count = 0
        self.external_send_count = 0

    def replay(self, command: GatewayCommand) -> DispatchReceipt | None:
        digest = self.request_digests.get(command.command_id)
        if digest is not None and digest != command.request_digest:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return self.receipts.get(command.command_id)

    def reject(self, command: GatewayCommand) -> DispatchReceipt:
        receipt = DispatchReceipt(
            command.command_id,
            command.request_digest,
            command.client_order_id,
            "REJECTED_LOCAL",
            0,
        )
        self.request_digests[command.command_id] = command.request_digest
        self.receipts[command.command_id] = receipt
        return receipt

    def record_attempt(self, command: GatewayCommand) -> None:
        if command.command_id in self.request_digests:
            raise ValueError("DUPLICATE_DISPATCH_ATTEMPT")
        self.request_digests[command.command_id] = command.request_digest
        self.dispatch_attempt_count += 1

    def record_send(self) -> None:
        self.external_send_count += 1

    def complete(self, command: GatewayCommand, status: str) -> DispatchReceipt:
        receipt = DispatchReceipt(
            command.command_id,
            command.request_digest,
            command.client_order_id,
            status,
            1,
        )
        self.receipts[command.command_id] = receipt
        return receipt


Sender = Callable[[GatewayCommand], TransportResult]


class GatewayDispatcher:
    def __init__(self, store: InMemoryDispatchStore, runtime: GatewayRuntimeBinding) -> None:
        self._store = store
        self._runtime = runtime

    def dispatch(
        self,
        command: GatewayCommand,
        authority: ActivationAuthority,
        send: Sender,
    ) -> DispatchReceipt:
        prior = self._store.replay(command)
        if prior is not None:
            return prior
        if not gateway_command_permitted(command, authority, self._runtime):
            return self._store.reject(command)
        self._store.record_attempt(command)
        self._store.record_send()
        try:
            result = send(command)
        except Exception:
            return self._store.complete(command, "SUBMISSION_UNKNOWN")
        if result.status == "ACKNOWLEDGED":
            return self._store.complete(command, "EXCHANGE_ACKNOWLEDGED")
        if result.status == "REJECTED":
            return self._store.complete(command, "RESOLVED_REJECTED")
        return self._store.complete(command, "SUBMISSION_UNKNOWN")


def gateway_command_permitted(
    command: GatewayCommand,
    authority: ActivationAuthority,
    runtime: GatewayRuntimeBinding,
) -> bool:
    identity_matches = (
        authority.deployment_enabled
        and authority.operator_active
        and authority.environment == "BINANCE_SPOT_TESTNET"
        and command.environment == authority.environment
        and command.account_generation == authority.account_generation
        and command.configuration_digest == authority.configuration_digest
        and command.allowlist_digest == authority.allowlist_digest
        and runtime.gateway_instance_id == authority.gateway_instance_id
        and runtime.build_digest == authority.build_digest
        and runtime.configuration_digest == authority.configuration_digest
        and runtime.allowlist_digest == authority.allowlist_digest
    )
    if not identity_matches:
        return False
    if command.effect_class == "CREATE_ORDER":
        return (
            command.command_type == "SUBMIT_LIMIT_ORDER"
            and authority.gateway_health == "READY"
            and not authority.paper_kill_active
            and not authority.testnet_barrier_active
            and authority.reconciliation_status == "HEALTHY"
        )
    if command.effect_class == "REDUCE_OR_CANCEL":
        return command.command_type == "CANCEL_EXISTING_ORDER" and authority.gateway_health in {
            "READY",
            "DEGRADED",
            "KILLED",
        }
    if command.effect_class == "OBSERVE_ONLY":
        return authority.gateway_health in {
            "ACTIVATING",
            "READY",
            "DEGRADED",
            "KILLED",
        } and command.command_type in {"QUERY_EXISTING_ORDER", "RECONCILIATION_OBSERVATION"}
    return False
