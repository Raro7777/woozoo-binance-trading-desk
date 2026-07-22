"""Least-privilege Phase 7 command ports.

The browser-facing control API authenticates the local operator and binds the
request.  It does not own Risk or Paper financial writes.  Concrete adapters
use the dedicated service database identities and return only durable command
receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import secrets
from typing import Literal, Protocol

import psycopg


@dataclass(frozen=True, slots=True)
class ActorBinding:
    actor_id: Literal["operator-local-1"]
    session_digest: str
    csrf_token_digest: str
    origin_hash: str


@dataclass(frozen=True, slots=True)
class ApprovalDecisionCommand:
    proposal_id: str
    decision: Literal["APPROVE", "REJECT"]
    expected_version: int
    paper_order_preview_hash: str
    idempotency_key: str
    request_hash: str
    reason: str
    binding: ActorBinding
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalRevocationCommand:
    approval_id: str
    expected_version: int
    idempotency_key: str
    request_hash: str
    reason: str
    binding: ActorBinding
    revoked_at: datetime


@dataclass(frozen=True, slots=True)
class KillActivationCommand:
    expected_version: int
    idempotency_key: str
    request_hash: str
    reason: str
    binding: ActorBinding
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class KillRecoveryCommand:
    expected_version: int
    activation_event_id: str
    incident_reference: str
    idempotency_key: str
    request_hash: str
    reason: str
    binding: ActorBinding
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PaperCancellationCommand:
    order_id: str
    expected_version: int
    idempotency_key: str
    request_hash: str
    reason: str
    binding: ActorBinding


@dataclass(frozen=True, slots=True)
class AgentAnalysisResult:
    created: bool
    run_id: str
    proposal_id: str | None
    outcome: str


@dataclass(frozen=True, slots=True)
class RiskEvaluationPortResult:
    created: bool
    decision_id: str


@dataclass(frozen=True, slots=True)
class PortResult:
    created: bool
    response: dict[str, object]


class CommandPortUnavailable(RuntimeError):
    """The dedicated authority adapter cannot safely accept the command."""


class CommandPortRejected(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class RiskCommandPort(Protocol):
    def decide_approval(self, command: ApprovalDecisionCommand) -> PortResult: ...

    def revoke_approval(self, command: ApprovalRevocationCommand) -> PortResult: ...

    def activate_kill(self, command: KillActivationCommand) -> PortResult: ...

    def recover_kill(self, command: KillRecoveryCommand) -> PortResult: ...


class AgentAnalysisPort(Protocol):
    def analyze_latest(
        self,
        symbol: str,
        observed_at: datetime,
        idempotency_key: str,
        request_hash: str,
    ) -> AgentAnalysisResult: ...


class RiskEvaluationPort(Protocol):
    def evaluate_proposal(
        self, proposal_id: str, paper_account_id: str, recorded_at: datetime
    ) -> RiskEvaluationPortResult: ...


class PaperCommandPort(Protocol):
    def attempt_authorization(self, authorization_id: str) -> PortResult: ...

    def consume_kill_activation(
        self, activation_event_id: str, payload_hash: str, observed_at: datetime
    ) -> tuple[str, ...]: ...

    def cancel_order(self, command: PaperCancellationCommand) -> PortResult: ...


class UnavailableRiskCommandPort:
    @staticmethod
    def _unavailable() -> PortResult:
        raise CommandPortUnavailable("Risk command authority is unavailable")

    def decide_approval(self, command: ApprovalDecisionCommand) -> PortResult:
        del command
        return self._unavailable()

    def revoke_approval(self, command: ApprovalRevocationCommand) -> PortResult:
        del command
        return self._unavailable()

    def activate_kill(self, command: KillActivationCommand) -> PortResult:
        del command
        return self._unavailable()

    def recover_kill(self, command: KillRecoveryCommand) -> PortResult:
        del command
        return self._unavailable()


class UnavailableAgentAnalysisPort:
    def analyze_latest(
        self,
        symbol: str,
        observed_at: datetime,
        idempotency_key: str,
        request_hash: str,
    ) -> AgentAnalysisResult:
        del symbol, observed_at, idempotency_key, request_hash
        raise CommandPortUnavailable("Agent analysis authority is unavailable")


class UnavailableRiskEvaluationPort:
    def evaluate_proposal(
        self, proposal_id: str, paper_account_id: str, recorded_at: datetime
    ) -> RiskEvaluationPortResult:
        del proposal_id, paper_account_id, recorded_at
        raise CommandPortUnavailable("Risk evaluation authority is unavailable")


class UnavailablePaperCommandPort:
    @staticmethod
    def _unavailable() -> PortResult:
        raise CommandPortUnavailable("Paper command authority is unavailable")

    def attempt_authorization(self, authorization_id: str) -> PortResult:
        del authorization_id
        return self._unavailable()

    def consume_kill_activation(
        self, activation_event_id: str, payload_hash: str, observed_at: datetime
    ) -> tuple[str, ...]:
        del activation_event_id, payload_hash, observed_at
        self._unavailable()
        raise AssertionError("unreachable")

    def cancel_order(self, command: PaperCancellationCommand) -> PortResult:
        del command
        return self._unavailable()


class PostgresAgentAnalysisPort:
    """IDs/time-only adapter over the Agent-owned Evidence and Proposal service."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("AGENT_DATABASE_URL_REQUIRED")
        self._database_url = database_url

    def analyze_latest(
        self,
        symbol: str,
        observed_at: datetime,
        idempotency_key: str,
        request_hash: str,
    ) -> AgentAnalysisResult:
        try:
            from agent_orchestrator import AgentAnalysisService, AnalysisCommand
            from agent_orchestrator.persistence import PostgresAgentStore

            store = PostgresAgentStore(self._database_url)
            replay = store.lookup_command_receipt(idempotency_key, request_hash)
            if replay is not None:
                return AgentAnalysisResult(
                    created=False,
                    run_id=replay.run_id,
                    proposal_id=replay.proposal_id,
                    outcome=replay.outcome,
                )
            service = AgentAnalysisService(
                store,
                clock=lambda: observed_at.isoformat().replace("+00:00", "Z"),
            )
            evidence_id = service.latest_healthy_evidence_id(symbol, observed_at)
            if evidence_id is None:
                raise CommandPortRejected(
                    "EVIDENCE_UNAVAILABLE", "healthy Evidence is unavailable", 409
                )
            result = service.execute(AnalysisCommand(evidence_id, idempotency_key, request_hash))
        except CommandPortRejected:
            raise
        except ValueError as exc:
            raise CommandPortRejected(str(exc), "Agent analysis held", 409) from exc
        except (RuntimeError, psycopg.Error) as exc:
            raise CommandPortUnavailable("Agent analysis authority is unavailable") from exc
        if result.persisted is None:
            raise CommandPortUnavailable("Agent authority omitted its durable receipt")
        return AgentAnalysisResult(
            created=result.persisted.created,
            run_id=result.persisted.run_id,
            proposal_id=result.persisted.proposal_id,
            outcome=result.status,
        )


class PostgresRiskEvaluationPort:
    """IDs/time-only adapter over authoritative Risk v3 assembly and persistence."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("RISK_DATABASE_URL_REQUIRED")
        from risk_engine import PostgresRiskStore, RiskDecisionService

        self._service = RiskDecisionService(PostgresRiskStore(database_url))

    def evaluate_proposal(
        self, proposal_id: str, paper_account_id: str, recorded_at: datetime
    ) -> RiskEvaluationPortResult:
        try:
            from risk_engine import RiskProposalCommand

            result = self._service.evaluate_proposal(
                RiskProposalCommand(proposal_id, paper_account_id, recorded_at)
            )
        except ValueError as exc:
            raise CommandPortRejected(str(exc), "Risk evaluation rejected", 409) from exc
        except (RuntimeError, psycopg.Error) as exc:
            raise CommandPortUnavailable("Risk evaluation authority is unavailable") from exc
        return RiskEvaluationPortResult(
            created=result.persisted.created,
            decision_id=result.persisted.decision_id,
        )


class PostgresRiskCommandPort:
    """Typed adapter for Risk-owned SECURITY DEFINER Phase 7 commands."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("RISK_DATABASE_URL_REQUIRED")
        self._database_url = database_url

    @staticmethod
    def _nonce() -> str:
        return "n" + secrets.token_urlsafe(31)

    @staticmethod
    def _database_rejection(exc: psycopg.Error) -> CommandPortRejected:
        # psycopg's rendered exception includes SQL/PLpgSQL context. Parameter
        # names such as p_idempotency_key must not influence public error codes.
        message = (exc.diag.message_primary or str(exc).splitlines()[0]).upper()
        mappings = (
            ("IDEMPOTENCY", "IDEMPOTENCY_CONFLICT", 409),
            ("VERSION", "VERSION_MISMATCH", 412),
            ("PREVIEW", "PREVIEW_HASH_MISMATCH", 409),
            ("KILL_RECOVERY_DATA_UNHEALTHY", "KILL_RECOVERY_UNHEALTHY", 409),
            (
                "KILL_RECOVERY_RECONCILIATION_UNHEALTHY",
                "RECONCILIATION_FAILED",
                409,
            ),
            ("KILL_RECOVERY_LEDGER_UNHEALTHY", "LEDGER_UNHEALTHY", 409),
            ("PAPER_WORKER_NOT_READY", "PAPER_WORKER_NOT_READY", 409),
            ("NOT READY", "APPROVAL_NOT_READY", 409),
            ("NOT_REVOCABLE", "APPROVAL_NOT_REVOCABLE", 409),
            ("DRIFT", "APPROVAL_NOT_READY", 409),
            ("EXPIRED", "AUTHORIZATION_EXPIRED", 409),
            ("REVOKED", "AUTHORIZATION_REVOKED", 409),
            ("KILL", "KILL_STATE_CONFLICT", 409),
            ("RECONCILIATION", "RECONCILIATION_FAILED", 409),
            ("LEDGER", "LEDGER_UNHEALTHY", 409),
        )
        for marker, code, status in mappings:
            if marker in message:
                return CommandPortRejected(code, "Risk command rejected", status)
        return CommandPortRejected("RISK_COMMAND_REJECTED", "Risk command rejected", 409)

    def decide_approval(self, command: ApprovalDecisionCommand) -> PortResult:
        try:
            with psycopg.connect(self._database_url) as connection:
                row = connection.execute(
                    "SELECT created,response FROM issue_paper_approval_v1("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        command.idempotency_key,
                        command.request_hash,
                        command.proposal_id,
                        "APPROVED" if command.decision == "APPROVE" else "REJECTED",
                        command.expected_version,
                        command.paper_order_preview_hash,
                        command.binding.actor_id,
                        command.binding.session_digest,
                        command.binding.csrf_token_digest,
                        command.binding.origin_hash,
                        self._nonce(),
                        self._nonce(),
                        command.decided_at,
                    ),
                ).fetchone()
        except psycopg.errors.UndefinedFunction as exc:
            raise CommandPortUnavailable("Risk approval adapter is unavailable") from exc
        except psycopg.Error as exc:
            raise self._database_rejection(exc) from exc
        if row is None or not isinstance(row[0], bool) or not isinstance(row[1], dict):
            raise CommandPortUnavailable("Risk authority returned an invalid approval receipt")
        return PortResult(row[0], row[1])

    def revoke_approval(self, command: ApprovalRevocationCommand) -> PortResult:
        try:
            with psycopg.connect(self._database_url) as connection:
                row = connection.execute(
                    "SELECT created,response FROM revoke_paper_approval_v1("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        command.idempotency_key,
                        command.request_hash,
                        command.approval_id,
                        command.expected_version,
                        command.reason,
                        command.binding.actor_id,
                        command.binding.session_digest,
                        command.binding.csrf_token_digest,
                        command.binding.origin_hash,
                        self._nonce(),
                        command.revoked_at,
                    ),
                ).fetchone()
        except psycopg.errors.UndefinedFunction as exc:
            raise CommandPortUnavailable("Risk revocation adapter is unavailable") from exc
        except psycopg.Error as exc:
            raise self._database_rejection(exc) from exc
        if row is None or not isinstance(row[0], bool) or not isinstance(row[1], dict):
            raise CommandPortUnavailable("Risk authority returned an invalid revocation receipt")
        revocation = row[1].get("revocation")
        if not isinstance(revocation, dict) or not isinstance(revocation.get("approval_id"), str):
            raise CommandPortUnavailable("Risk authority omitted revocation binding")
        return PortResult(
            row[0],
            {"result": row[1].get("result", "REVOKED"), "approval_id": revocation["approval_id"]},
        )

    def activate_kill(self, command: KillActivationCommand) -> PortResult:
        from risk_engine import KillActivation, PostgresKillSwitch

        try:
            with psycopg.connect(self._database_url) as orchestration:
                orchestration.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"control-kill:{command.idempotency_key}",),
                )
                prior = orchestration.execute(
                    "SELECT receipt.response,event.context_digest,event.reason,event.prior_version,"
                    "outbox.payload_hash FROM risk_kill_command_receipts receipt "
                    "JOIN kill_switch_events event USING(activation_event_id) "
                    "JOIN risk_outbox_links link ON link.aggregate_kind='kill-switch' "
                    "AND link.aggregate_id=event.activation_event_id "
                    "JOIN outbox_events outbox ON outbox.event_id=link.event_id "
                    "WHERE receipt.request_id=%s",
                    (command.idempotency_key,),
                ).fetchone()
                if prior is not None:
                    if (
                        prior[1] != command.request_hash
                        or prior[2] != command.reason
                        or prior[3] != command.expected_version
                    ):
                        raise CommandPortRejected(
                            "IDEMPOTENCY_CONFLICT", "Kill activation command changed", 409
                        )
                    prior_response = prior[0]
                    if not isinstance(prior_response, dict):
                        raise CommandPortUnavailable("Risk Kill receipt is invalid")
                    return PortResult(
                        False,
                        {
                            "result": "ACTIVATED",
                            "activation_event_id": prior_response["activation_event_id"],
                            "payload_hash": prior[4],
                            "kill_switch": {
                                "active": True,
                                "version": prior_response["version"],
                                "activation_event_id": prior_response["activation_event_id"],
                            },
                        },
                    )
                result = PostgresKillSwitch(self._database_url).activate(
                    KillActivation(
                        request_id=command.idempotency_key,
                        expected_version=command.expected_version,
                        trigger_kind="MANUAL",
                        actor_id="operator:operator-local-1",
                        reason_code="MANUAL_SAFETY_STOP",
                        reason=command.reason,
                        observed_at=command.observed_at,
                        context_digest=command.request_hash,
                    )
                )
                payload = orchestration.execute(
                    "SELECT payload_hash FROM outbox_events WHERE event_id=%s",
                    (result.outbox_event_id,),
                ).fetchone()
        except CommandPortRejected:
            raise
        except ValueError as exc:
            raise CommandPortRejected(str(exc), "Risk Kill activation rejected", 409) from exc
        except RuntimeError as exc:
            code = str(exc)
            status = (
                409
                if code in {"KILL_SWITCH_ALREADY_ACTIVE", "KILL_STATE_TRANSITION_CONFLICT"}
                else 503
            )
            if status == 409:
                raise CommandPortRejected(code, "Risk Kill activation rejected", status) from exc
            raise CommandPortUnavailable("Risk Kill activation adapter is unavailable") from exc
        except psycopg.Error as exc:
            raise CommandPortUnavailable("Risk Kill activation adapter is unavailable") from exc
        if payload is None:
            raise CommandPortUnavailable("Risk Kill activation outbox is unavailable")
        return PortResult(
            result.created,
            {
                "result": "ACTIVATED",
                "activation_event_id": result.activation_event_id,
                "payload_hash": payload[0],
                "kill_switch": {
                    "active": True,
                    "version": result.version,
                    "activation_event_id": result.activation_event_id,
                },
            },
        )

    def recover_kill(self, command: KillRecoveryCommand) -> PortResult:
        try:
            with psycopg.connect(self._database_url) as connection:
                row = connection.execute(
                    "SELECT created,response FROM recover_kill_switch_v1("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        command.idempotency_key,
                        command.request_hash,
                        command.expected_version,
                        command.activation_event_id,
                        command.incident_reference,
                        command.reason,
                        command.binding.actor_id,
                        command.binding.session_digest,
                        command.binding.csrf_token_digest,
                        command.binding.origin_hash,
                        command.observed_at,
                    ),
                ).fetchone()
        except psycopg.errors.UndefinedFunction as exc:
            raise CommandPortUnavailable("Risk Kill recovery adapter is unavailable") from exc
        except psycopg.Error as exc:
            raise self._database_rejection(exc) from exc
        if row is None or not isinstance(row[0], bool) or not isinstance(row[1], dict):
            raise CommandPortUnavailable("Risk authority returned an invalid recovery receipt")
        version = row[1].get("version")
        if not isinstance(version, int):
            raise CommandPortUnavailable("Risk authority omitted recovery version")
        response: dict[str, object] = {
            "result": "RECOVERED",
            "kill_switch": {
                "active": False,
                "version": version,
                "activation_event_id": command.activation_event_id,
            },
        }
        return PortResult(row[0], response)


class PostgresPaperCommandPort:
    """Adapter over the Paper-owned transactional store and its dedicated role."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("PAPER_DATABASE_URL_REQUIRED")
        from paper_engine.persistence import PostgresPaperStore

        self._database_url = database_url
        self._store = PostgresPaperStore(database_url)

    def attempt_authorization(self, authorization_id: str) -> PortResult:
        try:
            result = self._store.attempt_phase7_authorization(authorization_id)
        except KeyError as exc:
            raise CommandPortRejected(
                "AUTHORIZATION_NOT_FOUND", "Paper authorization is unavailable", 404
            ) from exc
        except ValueError as exc:
            code = str(exc)
            status_code = 409
            raise CommandPortRejected(
                code, "Paper authorization attempt rejected", status_code
            ) from exc
        except (RuntimeError, psycopg.Error) as exc:
            raise CommandPortUnavailable("Paper command authority is unavailable") from exc
        response = result.response
        if not isinstance(response, dict):
            raise CommandPortUnavailable("Paper authority returned an invalid receipt")
        normalized = dict(response)
        order_id = normalized.get("order_id")
        if normalized.get("result") == "CONSUMED_ORDER_CREATED" and isinstance(order_id, str):
            normalized["order"] = {"order_id": order_id}
        return PortResult(result.created, normalized)

    def consume_kill_activation(
        self, activation_event_id: str, payload_hash: str, observed_at: datetime
    ) -> tuple[str, ...]:
        try:
            while True:
                result = self._store.consume_kill_activation(
                    activation_event_id, payload_hash, received_at=observed_at
                )
                if not result.has_more:
                    break
            with psycopg.connect(self._database_url) as connection:
                rows = connection.execute(
                    "SELECT order_id FROM paper_kill_cancel_items "
                    "WHERE activation_event_id=%s ORDER BY order_id",
                    (activation_event_id,),
                ).fetchall()
        except ValueError as exc:
            raise CommandPortRejected(
                str(exc), "Kill cancellation command conflicted", 409
            ) from exc
        except (RuntimeError, psycopg.Error) as exc:
            raise CommandPortUnavailable("Paper Kill command authority is unavailable") from exc
        return tuple(row[0] for row in rows)

    def cancel_order(self, command: PaperCancellationCommand) -> PortResult:
        try:
            result = self._store.cancel_phase7_order(
                command.order_id,
                command.expected_version,
                command.idempotency_key,
                command.request_hash,
                command.reason,
            )
        except KeyError as exc:
            raise CommandPortRejected("ORDER_NOT_FOUND", "Paper order is unavailable", 404) from exc
        except ValueError as exc:
            code = str(exc)
            public_code = "VERSION_MISMATCH" if code == "ORDER_VERSION_MISMATCH" else code
            status_code = 412 if code == "ORDER_VERSION_MISMATCH" else 409
            raise CommandPortRejected(
                public_code, "Paper cancellation rejected", status_code
            ) from exc
        except (RuntimeError, psycopg.Error) as exc:
            raise CommandPortUnavailable("Paper command authority is unavailable") from exc
        if not isinstance(result.response, dict):
            raise CommandPortUnavailable("Paper authority returned an invalid receipt")
        return PortResult(result.created, result.response)
