"""Deterministic Phase 7 Paper Trading Room application service.

This module is a closed, local reference implementation for the browser/API
boundary.  It never contacts an exchange and accepts no account, balance,
price, quantity, fee, Risk verdict, actor, expiry, or authorization value from
the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
import json
import secrets
from threading import RLock
from typing import Callable, Literal, Protocol

import psycopg

from .command_ports import (
    ActorBinding,
    AgentAnalysisPort,
    ApprovalDecisionCommand,
    ApprovalRevocationCommand,
    CommandPortRejected,
    CommandPortUnavailable,
    KillActivationCommand,
    KillRecoveryCommand,
    PaperCancellationCommand,
    PaperCommandPort,
    RiskCommandPort,
    RiskEvaluationPort,
    UnavailableAgentAnalysisPort,
    UnavailablePaperCommandPort,
    UnavailableRiskCommandPort,
    UnavailableRiskEvaluationPort,
)
from .security import ACTOR_ID


PREVIEW_POLICY_VERSION = "woozoo.paper-order-preview-policy/v1"
RISK_POLICY_VERSION = "woozoo.risk-policy/v1"
APPROVAL_TTL = timedelta(minutes=5)
FEE_RATE = Decimal("0.001")
MAX_ORDER_NOTIONAL_RATIO = Decimal("0.0025")
PAPER_ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"
PAPER_WORKER_STATUSES = frozenset({"MISSING", "STALE", "HEALTHY", "FAILED", "STOPPED"})

SYMBOL_RULES = {
    "BTCUSDT": {
        "tick": Decimal("0.01"),
        "step": Decimal("0.00001"),
        "minimum": Decimal("5"),
    },
    "ETHUSDT": {
        "tick": Decimal("0.01"),
        "step": Decimal("0.0001"),
        "minimum": Decimal("5"),
    },
}

_BROWSER_AUDIT_SECRET_KEY_MARKERS = (
    "nonce",
    "binding",
    "session",
    "csrf",
    "origin",
    "credential",
    "password",
    "secret",
)


def browser_safe_audit_value(value: object) -> object:
    """Return a recursively sanitized browser projection without mutating raw audit data."""
    if isinstance(value, dict):
        return {
            key: browser_safe_audit_value(item)
            for key, item in value.items()
            if not any(
                marker in str(key).casefold() for marker in _BROWSER_AUDIT_SECRET_KEY_MARKERS
            )
        }
    if isinstance(value, list):
        return [browser_safe_audit_value(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_hash(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def floor_to(value: Decimal, quantum: Decimal) -> Decimal:
    return (value / quantum).to_integral_value(rounding=ROUND_DOWN) * quantum


@dataclass(frozen=True, slots=True)
class CommandResult:
    status_code: int
    body: dict[str, object]


@dataclass(frozen=True, slots=True)
class Receipt:
    request_hash: str
    result: CommandResult


class TradingRoomError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TradingRoom(Protocol):
    def dashboard(self) -> dict[str, object]: ...
    def create_analysis(self, symbol: str, idempotency_key: str) -> CommandResult: ...
    def get_analysis(self, run_id: str) -> dict[str, object]: ...
    def get_risk(self, risk_id: str) -> dict[str, object]: ...
    def approval_view(self, proposal_id: str) -> dict[str, object]: ...
    def decide_approval(
        self,
        *,
        proposal_id: str,
        decision: Literal["APPROVE", "REJECT"],
        expected_version: int,
        preview_hash: str,
        idempotency_key: str,
        reason: str,
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult: ...
    def revoke(
        self,
        approval_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult: ...
    def cancel_order(
        self,
        order_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult: ...
    def portfolio(self) -> dict[str, object]: ...
    def audit_events(self, after: int = 0) -> list[dict[str, object]]: ...
    def kill_switch(self) -> dict[str, object]: ...
    def activate_kill(
        self,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult: ...
    def recover_kill(
        self,
        expected_version: int,
        activation_event_id: str,
        incident_reference: str,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult: ...


class PaperWorkerReadinessAuthority(Protocol):
    def snapshot(self) -> dict[str, object]: ...


class PostgresPaperWorkerReadinessAuthority:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def snapshot(self) -> dict[str, object]:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT worker_name,status,instance_id,started_at,heartbeat_at,"
                "last_progress_at,last_result,last_error_code,stopped_at,ready,"
                "pending_authorizations,pending_kill_activations "
                "FROM paper_authorization_worker_reader_v1"
            ).fetchone()
        if row is None:
            raise TradingRoomError(
                "PAPER_WORKER_STATE_MISSING", "Paper worker state is unavailable", 503
            )
        return {
            "worker_name": row[0],
            "status": row[1],
            "instance_id": row[2],
            "started_at": row[3].isoformat().replace("+00:00", "Z") if row[3] else None,
            "heartbeat_at": row[4].isoformat().replace("+00:00", "Z") if row[4] else None,
            "last_progress_at": row[5].isoformat().replace("+00:00", "Z") if row[5] else None,
            "last_result": row[6],
            "last_error_code": row[7],
            "stopped_at": row[8].isoformat().replace("+00:00", "Z") if row[8] else None,
            "ready": row[9],
            "pending_authorizations": row[10],
            "pending_kill_activations": row[11],
        }


class InMemoryTradingRoom:
    """Deterministic local Paper authority with terminal authorization attempts."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._analysis: dict[str, dict[str, object]] = {}
        self._proposals: dict[str, dict[str, object]] = {}
        self._risks: dict[str, dict[str, object]] = {}
        self._approvals: dict[str, dict[str, object]] = {}
        self._revoked: set[str] = set()
        self._authorizations: dict[str, dict[str, object]] = {}
        self._authorization_by_approval: dict[str, str] = {}
        self._attempts: dict[str, dict[str, object]] = {}
        self._orders: dict[str, dict[str, object]] = {}
        self._receipts: dict[tuple[str, str], Receipt] = {}
        self._audit: list[dict[str, object]] = []
        self._sequence = 0
        self._kill_active = False
        self._kill_version = 0
        self._kill_event_id: str | None = None
        self._data_quality: Literal["HEALTHY", "STALE", "INVALID"] = "HEALTHY"
        self._reconciliation_healthy = True
        self._ledger_healthy = True
        self._portfolio_version = 1
        self._ledger_version = 1
        self._available_quote = Decimal("10000")
        self._held_quote = Decimal("0")
        self._available_base = {
            "BTCUSDT": Decimal("0"),
            "ETHUSDT": Decimal("0"),
        }
        self._held_base = {"BTCUSDT": Decimal("0"), "ETHUSDT": Decimal("0")}
        self._order_holds: dict[str, tuple[str, Decimal]] = {}
        self._ledger_entries: list[dict[str, object]] = []
        self._books = {
            "BTCUSDT": (Decimal("60000.00"), Decimal("60001.00")),
            "ETHUSDT": (Decimal("3000.00"), Decimal("3000.10")),
        }

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("trading-room clock must be timezone-aware")
        return value.astimezone(UTC)

    def _iso(self, value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    def _id(self, prefix: str, value: object) -> str:
        return canonical_hash({"kind": prefix, "value": value})

    def _audit_event(
        self,
        event_type: str,
        data: dict[str, object],
        *,
        producer: str,
        actor_id: str | None = None,
    ) -> dict[str, object]:
        self._sequence += 1
        event = {
            "sequence": self._sequence,
            "event_id": self._id("evt", {"sequence": self._sequence, "data": data}),
            "event_type": event_type,
            "producer": producer,
            "actor_id": actor_id,
            "occurred_at": self._iso(self._now()),
            "data": browser_safe_audit_value(data),
        }
        self._audit.append(event)
        return event

    def set_guard_state(
        self,
        *,
        data_quality: Literal["HEALTHY", "STALE", "INVALID"] | None = None,
        reconciliation_healthy: bool | None = None,
        ledger_healthy: bool | None = None,
    ) -> None:
        """Test/owned-service ingress; it is never registered as a browser route."""

        with self._lock:
            if data_quality is not None:
                self._data_quality = data_quality
            if reconciliation_healthy is not None:
                self._reconciliation_healthy = reconciliation_healthy
            if ledger_healthy is not None:
                self._ledger_healthy = ledger_healthy

    def _guard_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self._data_quality != "HEALTHY":
            reasons.append(f"DATA_{self._data_quality}")
        if self._kill_active:
            reasons.append("KILL_SWITCH_ACTIVE")
        if not self._reconciliation_healthy:
            reasons.append("RECONCILIATION_UNHEALTHY")
        if not self._ledger_healthy:
            reasons.append("LEDGER_UNHEALTHY")
        return reasons

    def dashboard(self) -> dict[str, object]:
        with self._lock:
            ready = not self._guard_reasons()
            return {
                "status": "READY" if ready else "HOLD",
                "trading_mode": "paper",
                "data_quality": self._data_quality,
                "kill_switch": self.kill_switch(),
                "reconciliation": "HEALTHY" if self._reconciliation_healthy else "FAILED",
                "ledger": "HEALTHY" if self._ledger_healthy else "FAILED",
                "markets": [
                    {
                        "symbol": symbol,
                        "best_bid": decimal_text(book[0]),
                        "best_ask": decimal_text(book[1]),
                        "quality": self._data_quality,
                    }
                    for symbol, book in self._books.items()
                ],
                "open_order_count": sum(
                    order["status"] in {"OPEN", "PARTIALLY_FILLED"}
                    for order in self._orders.values()
                ),
                "paper_worker": self._worker_state(),
            }

    def _worker_state(self) -> dict[str, object]:
        now = self._iso(self._now())
        return {
            "worker_name": "phase7-paper-authorization",
            "status": "HEALTHY",
            "instance_id": "in-memory-paper-worker",
            "started_at": now,
            "heartbeat_at": now,
            "last_progress_at": None,
            "last_result": None,
            "last_error_code": None,
            "stopped_at": None,
            "ready": True,
            "pending_authorizations": 0,
            "pending_kill_activations": 0,
        }

    def create_analysis(self, symbol: str, idempotency_key: str) -> CommandResult:
        if symbol not in SYMBOL_RULES:
            raise TradingRoomError("SYMBOL_NOT_ALLOWED", "symbol is not allowed", 422)
        body = {"symbol": symbol}
        request_hash = canonical_hash(
            {"method": "POST", "path": "/api/v1/analysis-runs", "actor": ACTOR_ID, "body": body}
        )
        with self._lock:
            replay = self._idempotent("analysis", idempotency_key, request_hash)
            if replay:
                return replay
            if self._guard_reasons():
                result = CommandResult(409, {"result": "HOLD", "reasons": self._guard_reasons()})
                return self._store_receipt("analysis", idempotency_key, request_hash, result)
            evidence_id = self._id(
                "evidence", {"symbol": symbol, "quality": self._data_quality, "seq": self._sequence}
            )
            run_payload = {
                "schema_version": "woozoo.analysis-run-view/v1",
                "namespace": "paper",
                "symbol": symbol,
                "evidence_id": evidence_id,
                "provider": "mock",
                "tool_count": 0,
            }
            run_id = self._id("run", run_payload)
            proposal_payload = {
                "schema_version": "woozoo.trade-proposal/v1",
                "proposal_id": self._id("proposal", {"run_id": run_id, "side": "BUY"}),
                "symbol": symbol,
                "side": "BUY",
            }
            proposal_hash = canonical_hash(proposal_payload)
            proposal_id = str(proposal_payload["proposal_id"])
            preview = self._build_preview(symbol, "BUY")
            risk_input = {
                "schema_version": "woozoo.risk-input/v3",
                "namespace": "paper",
                "preview_policy_version": PREVIEW_POLICY_VERSION,
                "proposal_hash": proposal_hash,
                "evidence_id": evidence_id,
                "preview": preview,
                "risk_policy_version": RISK_POLICY_VERSION,
                "portfolio_version": self._portfolio_version,
                "ledger_version": self._ledger_version,
                "kill_version": self._kill_version,
            }
            risk_input_digest = canonical_hash(risk_input)
            decision_as_of = self._iso(self._now())
            portfolio_snapshot_hash = canonical_hash(
                {
                    "available_quote": decimal_text(self._available_quote),
                    "held_quote": decimal_text(self._held_quote),
                    "positions": {
                        item: decimal_text(self._available_base[item] + self._held_base[item])
                        for item in self._available_base
                    },
                    "version": self._portfolio_version,
                }
            )
            data_state_hash = canonical_hash(
                {"quality": self._data_quality, "evidence_id": evidence_id}
            )
            reconciliation_hash = canonical_hash(
                {"healthy": self._reconciliation_healthy, "version": self._ledger_version}
            )
            unsigned_risk = {
                "decision_schema_version": "woozoo.risk-decision/v1",
                "risk_input_digest": risk_input_digest,
                "verdict": "ALLOWED",
                "primary_reason": "RISK_ALLOWED",
                "ordered_reason_codes": ["RISK_ALLOWED"],
                "policy_version": RISK_POLICY_VERSION,
                "proposal_hash": proposal_hash,
                "portfolio_snapshot_hash": portfolio_snapshot_hash,
                "data_state_hash": data_state_hash,
                "paper_order_preview_hash": preview["paper_order_preview_hash"],
                "reconciliation_checkpoint_hash": reconciliation_hash,
                "kill_switch_version": self._kill_version,
                "decision_as_of": decision_as_of,
            }
            risk_hash = canonical_hash(unsigned_risk)
            risk_id = self._id("risk", {"decision_hash": risk_hash})
            risk_payload = {
                **unsigned_risk,
                "decision_id": risk_id,
                "decision_hash": risk_hash,
            }
            record = {
                **run_payload,
                "run_id": run_id,
                "status": "COMPLETED",
                "report": {
                    "summary": "모의 제공자가 재생 가능한 모의투자 연구 결과를 생성했습니다.",
                    "confidence": "0.50",
                    "hold_reasons": [],
                },
                "proposal_id": proposal_id,
                "risk_decision_id": risk_id,
            }
            self._analysis[run_id] = record
            self._proposals[proposal_id] = {
                **proposal_payload,
                "proposal_hash": proposal_hash,
                "run_id": run_id,
                "evidence_id": evidence_id,
                "version": 1,
            }
            self._risks[risk_id] = {
                **risk_payload,
                "_proposal_id": proposal_id,
                "_preview": preview,
            }
            self._audit_event(
                "ANALYSIS_COMPLETED",
                {"run_id": run_id, "proposal_id": proposal_id, "risk_decision_id": risk_id},
                producer="agent-orchestrator",
            )
            result = CommandResult(201, record)
            return self._store_receipt("analysis", idempotency_key, request_hash, result)

    def _build_preview(self, symbol: str, side: str) -> dict[str, object]:
        rules = SYMBOL_RULES[symbol]
        best_bid, best_ask = self._books[symbol]
        raw_price = best_ask if side == "BUY" else best_bid
        price = floor_to(raw_price, rules["tick"])
        equity = (
            self._available_quote
            + self._held_quote
            + sum(
                (self._available_base[item] + self._held_base[item]) * self._books[item][0]
                for item in self._available_base
            )
        )
        max_notional = equity * MAX_ORDER_NOTIONAL_RATIO
        if side == "BUY":
            principal_budget = min(self._available_quote, max_notional) / (Decimal(1) + FEE_RATE)
            quantity = floor_to(principal_budget / price, rules["step"])
        else:
            quantity = floor_to(
                min(self._available_base[symbol], max_notional / price), rules["step"]
            )
        principal = quantity * price
        fee = principal * FEE_RATE
        worst_notional = principal + fee
        preview_without_hash: dict[str, object] = {
            "symbol": symbol,
            "side": side,
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "quantity": decimal_text(quantity),
            "limit_price": decimal_text(price),
            "worst_case_fee": decimal_text(fee),
            "worst_case_hold": decimal_text(worst_notional if side == "BUY" else quantity),
            "worst_case_notional": decimal_text(worst_notional),
            "best_bid": decimal_text(best_bid),
            "best_ask": decimal_text(best_ask),
            "expected_slippage_inputs": {"method": "limit-vs-book-v1"},
        }
        if quantity <= 0 or principal < rules["minimum"]:
            raise TradingRoomError("PREVIEW_BELOW_MINIMUM", "no safe Paper quantity is available")
        return {
            **preview_without_hash,
            "paper_order_preview_hash": canonical_hash(preview_without_hash),
        }

    def get_analysis(self, run_id: str) -> dict[str, object]:
        with self._lock:
            if run_id not in self._analysis:
                raise TradingRoomError("ANALYSIS_NOT_FOUND", "analysis run is unavailable", 404)
            return self._analysis[run_id]

    def get_risk(self, risk_id: str) -> dict[str, object]:
        with self._lock:
            if risk_id not in self._risks:
                raise TradingRoomError("RISK_NOT_FOUND", "risk decision is unavailable", 404)
            return {
                key: value for key, value in self._risks[risk_id].items() if not key.startswith("_")
            }

    def approval_view(self, proposal_id: str) -> dict[str, object]:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                raise TradingRoomError("PROPOSAL_NOT_FOUND", "proposal is unavailable", 404)
            risk = self._risks[str(self._analysis[str(proposal["run_id"])]["risk_decision_id"])]
            approvals = [
                value for value in self._approvals.values() if value["proposal_id"] == proposal_id
            ]
            approval = approvals[-1] if approvals else None
            authorization = None
            if approval is not None:
                auth_id = self._authorization_by_approval.get(str(approval["approval_id"]))
                authorization = self._authorizations.get(auth_id) if auth_id else None
            reasons = self._guard_reasons()
            now = self._now()
            if risk["verdict"] != "ALLOWED":
                status = "BLOCKED"
            elif approval is None and reasons:
                status = "BLOCKED"
            elif approval is None:
                status = "READY"
            elif approval["decision"] == "REJECTED":
                status = "BLOCKED"
                reasons = ["OPERATOR_REJECTED"]
            elif str(approval["approval_id"]) in self._revoked:
                status = "BLOCKED"
                reasons = ["APPROVAL_REVOKED"]
            elif now >= datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00")):
                status = "BLOCKED"
                reasons = ["APPROVAL_EXPIRED"]
            elif authorization is None:
                status = "APPROVED"
            elif str(authorization["authorization_id"]) in self._attempts:
                status = "AUTHORIZATION_ISSUED"
            else:
                status = "AUTHORIZATION_ISSUED"
            approval_status: str | None = None
            if approval is not None:
                approval_status = str(approval["decision"])
                if str(approval["approval_id"]) in self._revoked:
                    approval_status = "REVOKED"
                elif now >= datetime.fromisoformat(
                    str(approval["expires_at"]).replace("Z", "+00:00")
                ):
                    approval_status = "EXPIRED"
            authorization_status: str | None = None
            if authorization is not None:
                authorization_status = "ISSUED"
                attempt = self._attempts.get(str(authorization["authorization_id"]))
                if attempt is not None:
                    authorization_status = (
                        "BLOCKED" if attempt["result"] == "BLOCKED" else "CONSUMED"
                    )
            return {
                "proposal_id": proposal_id,
                "proposal_hash": proposal["proposal_hash"],
                "status": status,
                "reason_codes": reasons,
                "risk_decision_id": risk["decision_id"],
                "risk_decision_hash": risk["decision_hash"],
                "risk_input_digest": risk["risk_input_digest"],
                "risk_policy_version": risk["policy_version"],
                "risk_verdict": risk["verdict"],
                "paper_order_preview": risk["_preview"],
                "paper_order_preview_hash": risk["paper_order_preview_hash"],
                "approval_id": approval["approval_id"] if approval is not None else None,
                "approval_status": approval_status,
                "authorization_id": (
                    authorization["authorization_id"] if authorization is not None else None
                ),
                "authorization_status": authorization_status,
                "approval_action_allowed": status == "READY",
                "approve_action_allowed": status == "READY",
                "reject_action_allowed": status == "READY",
                "approval_ttl_seconds": 300,
                "approval_expires_at": (approval["expires_at"] if approval is not None else None),
                "view_version": proposal["version"],
                "served_at": self._iso(now),
            }

    def decide_approval(
        self,
        *,
        proposal_id: str,
        decision: Literal["APPROVE", "REJECT"],
        expected_version: int,
        preview_hash: str,
        idempotency_key: str,
        reason: str,
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        body = {
            "proposal_id": proposal_id,
            "decision": decision,
            "expected_version": expected_version,
            "paper_order_preview_hash": preview_hash,
            "reason": reason,
        }
        request_hash = canonical_hash(
            {"method": "POST", "path": "/api/v1/paper-approvals", "actor": ACTOR_ID, "body": body}
        )
        with self._lock:
            replay = self._idempotent("approval", idempotency_key, request_hash)
            if replay:
                return replay
            view = self.approval_view(proposal_id)
            if expected_version != view["view_version"]:
                raise TradingRoomError("VERSION_MISMATCH", "approval view version changed", 412)
            if preview_hash != view["paper_order_preview_hash"]:
                raise TradingRoomError("PREVIEW_HASH_MISMATCH", "preview binding changed", 409)
            if view["status"] != "READY":
                raise TradingRoomError("APPROVAL_NOT_READY", "proposal is not ready for approval")
            now = self._now()
            approval_id = self._id(
                "approval",
                {"proposal_id": proposal_id, "decision": decision, "nonce": secrets.token_hex(16)},
            )
            approval: dict[str, object] = {
                "approval_id": approval_id,
                "proposal_id": proposal_id,
                "proposal_hash": view["proposal_hash"],
                "risk_decision_id": view["risk_decision_id"],
                "risk_decision_hash": view["risk_decision_hash"],
                "risk_input_digest": view["risk_input_digest"],
                "risk_policy_version": view["risk_policy_version"],
                "paper_order_preview": view["paper_order_preview"],
                "paper_order_preview_hash": preview_hash,
                "actor_id": ACTOR_ID,
                "session_binding_hash": session_binding_hash
                or canonical_hash({"reference": "local-session"}),
                "csrf_binding_hash": csrf_binding_hash
                or canonical_hash({"reference": "one-time-csrf"}),
                "origin_hash": origin_hash or canonical_hash({"origin": "https://localhost"}),
                "decision": "APPROVED" if decision == "APPROVE" else "REJECTED",
                "approval_nonce": f"n{secrets.token_urlsafe(24)}",
                "expected_kill_switch_version": self._kill_version,
                "expected_portfolio_version": self._portfolio_version,
                "expected_ledger_version": self._ledger_version,
                "decided_at": self._iso(now),
                "expires_at": self._iso(now + APPROVAL_TTL),
            }
            approval["payload_hash"] = canonical_hash(approval)
            self._approvals[approval_id] = approval
            self._audit_event(
                "PAPER_APPROVAL_RECORDED",
                {"approval_id": approval_id, "decision": approval["decision"]},
                producer="risk-engine",
                actor_id=ACTOR_ID,
            )
            if decision == "REJECT":
                result = CommandResult(201, {"result": "REJECTED", "approval": approval})
                return self._store_receipt("approval", idempotency_key, request_hash, result)
            result = self._authorize_and_attempt(approval, view)
            return self._store_receipt("approval", idempotency_key, request_hash, result)

    def _authorize_and_attempt(
        self, approval: dict[str, object], view: dict[str, object]
    ) -> CommandResult:
        now = self._now()
        auth_id = self._id(
            "authorization",
            {"approval_id": approval["approval_id"], "nonce": secrets.token_hex(16)},
        )
        authorization = {
            "authorization_id": auth_id,
            "namespace": "paper",
            "approval_id": approval["approval_id"],
            "approval_hash": approval["payload_hash"],
            "approval_nonce_hash": sha256(
                str(approval["approval_nonce"]).encode("utf-8")
            ).hexdigest(),
            "authorization_nonce": f"n{secrets.token_urlsafe(24)}",
            "proposal_id": view["proposal_id"],
            "proposal_hash": view["proposal_hash"],
            "risk_decision_id": view["risk_decision_id"],
            "risk_decision_hash": view["risk_decision_hash"],
            "risk_input_digest": view["risk_input_digest"],
            "risk_policy_version": view["risk_policy_version"],
            "paper_order_preview_hash": approval["paper_order_preview_hash"],
            "current_data_state_hash": canonical_hash(
                {
                    "quality": self._data_quality,
                    "books": {
                        symbol: [decimal_text(book[0]), decimal_text(book[1])]
                        for symbol, book in self._books.items()
                    },
                }
            ),
            "current_data_as_of": self._iso(now),
            "current_knowledge_cutoff": self._iso(now),
            "kill_switch_version": self._kill_version,
            "reconciliation_checkpoint_hash": canonical_hash(
                {"healthy": self._reconciliation_healthy, "version": self._portfolio_version}
            ),
            "ledger_snapshot_hash": canonical_hash(
                {"healthy": self._ledger_healthy, "version": self._ledger_version}
            ),
            "paper_account_id": canonical_hash({"namespace": "paper", "actor": ACTOR_ID}),
            "issued_at": self._iso(now),
            "expires_at": approval["expires_at"],
        }
        authorization["authorization_input_digest"] = canonical_hash(authorization)
        self._authorizations[auth_id] = authorization
        self._authorization_by_approval[str(approval["approval_id"])] = auth_id

        reasons = self._guard_reasons()
        approval_expiry = datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
        if now >= approval_expiry:
            reasons.append("APPROVAL_EXPIRED")
        if view["paper_order_preview_hash"] != approval["paper_order_preview_hash"]:
            reasons.append("PREVIEW_HASH_MISMATCH")
        if reasons:
            attempt: dict[str, object] = {
                "authorization_id": auth_id,
                "result": "BLOCKED",
                "reasons": reasons,
                "attempted_at": self._iso(now),
            }
            self._attempts[auth_id] = attempt
            self._audit_event("PAPER_AUTHORIZATION_BLOCKED", attempt, producer="paper-engine")
            return CommandResult(
                409,
                {
                    "result": "BLOCKED",
                    "approval": approval,
                    "authorization": authorization,
                    "attempt": attempt,
                },
            )

        preview = view["paper_order_preview"]
        assert isinstance(preview, dict)
        order_id = self._id("paper_order", {"authorization_id": auth_id})
        order = {
            "order_id": order_id,
            "client_order_id": canonical_hash({"authorization_id": auth_id, "kind": "client"}),
            "authorization_id": auth_id,
            "authorization_namespace": "paper",
            "authorization_nonce": authorization["authorization_nonce"],
            "approval_id": approval["approval_id"],
            "proposal_hash": view["proposal_hash"],
            "risk_decision_hash": view["risk_decision_hash"],
            "paper_order_preview_hash": view["paper_order_preview_hash"],
            "symbol": preview["symbol"],
            "side": preview["side"],
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "quantity": preview["quantity"],
            "limit_price": preview["limit_price"],
            "filled_quantity": "0",
            "status": "OPEN",
            "version": 1,
        }
        self._orders[order_id] = order
        hold_amount = Decimal(str(preview["worst_case_hold"]))
        if preview["side"] == "BUY":
            if hold_amount > self._available_quote:
                return self._block_authorization(
                    auth_id, authorization, approval, ["INSUFFICIENT_FUNDS"]
                )
            self._available_quote -= hold_amount
            self._held_quote += hold_amount
            self._order_holds[order_id] = ("USDT", hold_amount)
            self._journal_hold(order_id, "USDT", hold_amount, "HOLD")
        else:
            symbol = str(preview["symbol"])
            if hold_amount > self._available_base[symbol]:
                return self._block_authorization(
                    auth_id, authorization, approval, ["INSUFFICIENT_POSITION"]
                )
            self._available_base[symbol] -= hold_amount
            self._held_base[symbol] += hold_amount
            self._order_holds[order_id] = (symbol.replace("USDT", ""), hold_amount)
            self._journal_hold(order_id, symbol.replace("USDT", ""), hold_amount, "HOLD")
        self._attempts[auth_id] = {
            "authorization_id": auth_id,
            "result": "CONSUMED_ORDER_CREATED",
            "order_id": order_id,
            "reasons": [],
            "attempted_at": self._iso(now),
        }
        self._portfolio_version += 1
        self._ledger_version += 1
        self._audit_event(
            "PAPER_ORDER_CREATED",
            {"order_id": order_id, "authorization_id": auth_id},
            producer="paper-engine",
        )
        return CommandResult(
            201,
            {
                "result": "CONSUMED_ORDER_CREATED",
                "approval": approval,
                "authorization": authorization,
                "order": order,
            },
        )

    def _block_authorization(
        self,
        authorization_id: str,
        authorization: dict[str, object],
        approval: dict[str, object],
        reasons: list[str],
    ) -> CommandResult:
        self._orders.pop(self._id("paper_order", {"authorization_id": authorization_id}), None)
        attempt: dict[str, object] = {
            "authorization_id": authorization_id,
            "result": "BLOCKED",
            "reasons": reasons,
            "attempted_at": self._iso(self._now()),
        }
        self._attempts[authorization_id] = attempt
        self._audit_event("PAPER_AUTHORIZATION_BLOCKED", attempt, producer="paper-engine")
        return CommandResult(
            409,
            {
                "result": "BLOCKED",
                "approval": approval,
                "authorization": authorization,
                "attempt": attempt,
            },
        )

    def _journal_hold(self, order_id: str, asset: str, amount: Decimal, action: str) -> None:
        journal_id = canonical_hash(
            {"order_id": order_id, "asset": asset, "amount": decimal_text(amount), "action": action}
        )
        self._ledger_entries.extend(
            [
                {
                    "journal_id": journal_id,
                    "asset": asset,
                    "account": f"Assets:Paper:{asset}:Held",
                    "side": "DEBIT" if action == "HOLD" else "CREDIT",
                    "amount": decimal_text(amount),
                },
                {
                    "journal_id": journal_id,
                    "asset": asset,
                    "account": f"Assets:Paper:{asset}:Available",
                    "side": "CREDIT" if action == "HOLD" else "DEBIT",
                    "amount": decimal_text(amount),
                },
            ]
        )

    def _release_order_hold(self, order_id: str) -> None:
        hold = self._order_holds.pop(order_id, None)
        if hold is None:
            return
        asset, amount = hold
        if asset == "USDT":
            self._held_quote -= amount
            self._available_quote += amount
        else:
            symbol = f"{asset}USDT"
            self._held_base[symbol] -= amount
            self._available_base[symbol] += amount
        self._journal_hold(order_id, asset, amount, "RELEASE")

    def revoke(
        self,
        approval_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": f"/api/v1/paper-approvals/{approval_id}/revocations",
                "actor": ACTOR_ID,
                "body": {"expected_version": expected_version, "reason": reason},
            }
        )
        idempotency_key = idempotency_key or request_hash
        with self._lock:
            replay = self._idempotent("approval-revocation", idempotency_key, request_hash)
            if replay:
                return replay
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise TradingRoomError("APPROVAL_NOT_FOUND", "approval is unavailable", 404)
            if expected_version != 1:
                raise TradingRoomError("VERSION_MISMATCH", "approval version changed", 412)
            if approval_id in self._revoked:
                result = CommandResult(
                    200, {"result": "ALREADY_REVOKED", "approval_id": approval_id}
                )
                return self._store_receipt(
                    "approval-revocation", idempotency_key, request_hash, result
                )
            auth_id = self._authorization_by_approval.get(approval_id)
            if auth_id and auth_id in self._attempts:
                raise TradingRoomError(
                    "AUTHORIZATION_ALREADY_ATTEMPTED", "authorization is terminal"
                )
            self._revoked.add(approval_id)
            self._audit_event(
                "PAPER_APPROVAL_REVOKED",
                {"approval_id": approval_id, "reason": reason},
                producer="risk-engine",
                actor_id=ACTOR_ID,
            )
            result = CommandResult(201, {"result": "REVOKED", "approval_id": approval_id})
            return self._store_receipt("approval-revocation", idempotency_key, request_hash, result)

    def cancel_order(
        self,
        order_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        del idempotency_key, session_binding_hash, csrf_binding_hash, origin_hash
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise TradingRoomError("ORDER_NOT_FOUND", "Paper order is unavailable", 404)
            if expected_version != order["version"]:
                raise TradingRoomError("VERSION_MISMATCH", "order version changed", 412)
            if order["status"] not in {"OPEN", "PARTIALLY_FILLED"}:
                return CommandResult(200, {"result": "ALREADY_TERMINAL", "order": order})
            current_version = order["version"]
            assert isinstance(current_version, int)
            order = {**order, "status": "CANCELLED", "version": current_version + 1}
            self._orders[order_id] = order
            self._release_order_hold(order_id)
            self._portfolio_version += 1
            self._ledger_version += 1
            self._audit_event(
                "PAPER_ORDER_CANCELLED",
                {"order_id": order_id, "reason": reason},
                producer="paper-engine",
                actor_id=ACTOR_ID,
            )
            return CommandResult(201, {"result": "CANCELLED", "order": order})

    def portfolio(self) -> dict[str, object]:
        with self._lock:
            browser_orders = [
                {
                    key: value
                    for key, value in order.items()
                    if key not in {"authorization_nonce", "authorization_namespace"}
                }
                for order in self._orders.values()
            ]
            return {
                "namespace": "paper",
                "portfolio_version": self._portfolio_version,
                "ledger_version": self._ledger_version,
                "available_quote": decimal_text(self._available_quote),
                "held_quote": decimal_text(self._held_quote),
                "positions": {
                    symbol: {
                        "available": decimal_text(value),
                        "held": decimal_text(self._held_base[symbol]),
                    }
                    for symbol, value in self._available_base.items()
                },
                "orders": browser_orders,
                "ledger_entries": list(self._ledger_entries),
                "ledger_health": "HEALTHY" if self._ledger_healthy else "FAILED",
                "reconciliation_health": "HEALTHY" if self._reconciliation_healthy else "FAILED",
                "ledger_checkpoint_id": None,
                "reconciliation_as_of": None,
                "reconciliation_input_digest": None,
                "reconciliation_status": ("HEALTHY" if self._reconciliation_healthy else "FAILED"),
                "checkpoint_authority_sequence": 0,
                "current_authority_sequence": 0,
                "ledger_status": "BALANCED" if self._ledger_healthy else "UNBALANCED",
                "ledger_imbalance_count": 0 if self._ledger_healthy else 1,
            }

    def audit_events(self, after: int = 0) -> list[dict[str, object]]:
        with self._lock:
            result: list[dict[str, object]] = []
            for event in self._audit:
                sequence = event["sequence"]
                assert isinstance(sequence, int)
                if sequence > after:
                    safe_event = browser_safe_audit_value(event)
                    assert isinstance(safe_event, dict)
                    result.append(safe_event)
            return result

    def kill_switch(self) -> dict[str, object]:
        return {
            "active": self._kill_active,
            "version": self._kill_version,
            "activation_event_id": self._kill_event_id,
            "recovery_event_id": None,
            "cancellation_status": "NOT_ACTIVE" if not self._kill_active else "COMPLETE",
            "cancellation_completed_at": None,
            "open_order_count": sum(
                order["status"] in {"OPEN", "PARTIALLY_FILLED"} for order in self._orders.values()
            ),
            "data_status": "HEALTHY" if self._data_quality == "HEALTHY" else "UNHEALTHY",
            "reconciliation_status": ("HEALTHY" if self._reconciliation_healthy else "UNHEALTHY"),
            "ledger_status": "BALANCED" if self._ledger_healthy else "UNBALANCED",
            "recovery_allowed": bool(
                self._kill_active
                and self._data_quality == "HEALTHY"
                and self._reconciliation_healthy
                and self._ledger_healthy
                and not any(
                    order["status"] in {"OPEN", "PARTIALLY_FILLED"}
                    for order in self._orders.values()
                )
            ),
            "paper_worker": self._worker_state(),
        }

    def activate_kill(
        self,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": "/api/v1/kill-switch/activate",
                "actor": ACTOR_ID,
                "body": {"expected_version": expected_version, "reason": reason},
            }
        )
        idempotency_key = idempotency_key or request_hash
        with self._lock:
            replay = self._idempotent("kill-activation", idempotency_key, request_hash)
            if replay:
                return replay
            if expected_version != self._kill_version:
                raise TradingRoomError("VERSION_MISMATCH", "Kill version changed", 412)
            if self._kill_active:
                result = CommandResult(
                    200,
                    {
                        "result": "ALREADY_ACTIVE",
                        "kill_switch": {
                            "active": True,
                            "version": self._kill_version,
                            "activation_event_id": self._kill_event_id,
                        },
                        "cancelled_order_ids": [],
                    },
                )
                return self._store_receipt("kill-activation", idempotency_key, request_hash, result)
            self._kill_active = True
            self._kill_version += 1
            event = self._audit_event(
                "KILL_SWITCH_ACTIVATED",
                {"version": self._kill_version, "reason": reason},
                producer="risk-engine",
                actor_id=ACTOR_ID,
            )
            self._kill_event_id = str(event["event_id"])
            cancelled: list[str] = []
            for order_id in sorted(self._orders):
                order = self._orders[order_id]
                if order["status"] in {"OPEN", "PARTIALLY_FILLED"}:
                    current_version = order["version"]
                    assert isinstance(current_version, int)
                    self._orders[order_id] = {
                        **order,
                        "status": "CANCELLED",
                        "version": current_version + 1,
                    }
                    self._release_order_hold(order_id)
                    cancelled.append(order_id)
                    self._audit_event(
                        "PAPER_ORDER_CANCELLED_BY_KILL",
                        {"order_id": order_id, "activation_event_id": self._kill_event_id},
                        producer="paper-engine",
                    )
            result = CommandResult(
                201,
                {
                    "result": "ACTIVATED",
                    "kill_switch": {
                        "active": True,
                        "version": self._kill_version,
                        "activation_event_id": self._kill_event_id,
                    },
                    "cancelled_order_ids": cancelled,
                },
            )
            return self._store_receipt("kill-activation", idempotency_key, request_hash, result)

    def recover_kill(
        self,
        expected_version: int,
        activation_event_id: str,
        incident_reference: str,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        del session_binding_hash, csrf_binding_hash, origin_hash
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": "/api/v1/kill-switch/recover",
                "actor": ACTOR_ID,
                "body": {
                    "expected_version": expected_version,
                    "activation_event_id": activation_event_id,
                    "incident_reference": incident_reference,
                    "reason": reason,
                },
            }
        )
        idempotency_key = idempotency_key or request_hash
        with self._lock:
            replay = self._idempotent("kill-recovery", idempotency_key, request_hash)
            if replay:
                return replay
            if expected_version != self._kill_version:
                raise TradingRoomError("VERSION_MISMATCH", "Kill version changed", 412)
            if not self._kill_active or activation_event_id != self._kill_event_id:
                raise TradingRoomError("KILL_RECOVERY_MISMATCH", "active Kill event does not match")
            if (
                self._data_quality != "HEALTHY"
                or not self._reconciliation_healthy
                or not self._ledger_healthy
            ):
                raise TradingRoomError(
                    "KILL_RECOVERY_UNHEALTHY", "recovery evidence is not healthy"
                )
            self._kill_active = False
            self._kill_version += 1
            self._audit_event(
                "KILL_SWITCH_RECOVERED",
                {
                    "activation_event_id": activation_event_id,
                    "incident_reference": incident_reference,
                    "reason": reason,
                    "version": self._kill_version,
                },
                producer="risk-engine",
                actor_id=ACTOR_ID,
            )
            result = CommandResult(
                201,
                {
                    "result": "RECOVERED",
                    "kill_switch": {
                        "active": False,
                        "version": self._kill_version,
                        "activation_event_id": self._kill_event_id,
                    },
                },
            )
            return self._store_receipt("kill-recovery", idempotency_key, request_hash, result)

    def _idempotent(self, scope: str, key: str, request_hash: str) -> CommandResult | None:
        if not key or len(key) > 128:
            raise TradingRoomError(
                "IDEMPOTENCY_KEY_REQUIRED", "valid idempotency key required", 400
            )
        receipt = self._receipts.get((scope, key))
        if receipt is None:
            return None
        if receipt.request_hash != request_hash:
            raise TradingRoomError("IDEMPOTENCY_CONFLICT", "idempotency key body changed", 409)
        return receipt.result

    def _store_receipt(
        self, scope: str, key: str, request_hash: str, result: CommandResult
    ) -> CommandResult:
        self._receipts[(scope, key)] = Receipt(request_hash=request_hash, result=result)
        return result


class PostgresTradingRoom:
    """Restart-safe Phase 7 read authority with fail-closed mutation boundaries.

    Financial rows are read only through least-privilege projections. Mutation
    methods deliberately fail until the Risk and Paper service-owned transactional
    command adapters are available; control-api must never fall back to process
    memory or acquire direct financial-table write authority.
    """

    def __init__(
        self,
        database_url: str,
        *,
        risk_commands: RiskCommandPort | None = None,
        paper_commands: PaperCommandPort | None = None,
        agent_analysis: AgentAnalysisPort | None = None,
        risk_evaluation: RiskEvaluationPort | None = None,
        worker_readiness: PaperWorkerReadinessAuthority | None = None,
        clock: Callable[[], datetime] | None = None,
        after_authorization_issued: Callable[[str], None] | None = None,
    ) -> None:
        if not database_url:
            raise ValueError("CONTROL_DATABASE_URL_REQUIRED")
        self.database_url = database_url
        self._risk_commands = risk_commands or UnavailableRiskCommandPort()
        self._paper_commands = paper_commands or UnavailablePaperCommandPort()
        self._agent_analysis = agent_analysis or UnavailableAgentAnalysisPort()
        self._risk_evaluation = risk_evaluation or UnavailableRiskEvaluationPort()
        self._worker_readiness = worker_readiness or PostgresPaperWorkerReadinessAuthority(
            database_url
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._after_authorization_issued = after_authorization_issued

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("trading-room clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _binding(
        session_binding_hash: str | None,
        csrf_binding_hash: str | None,
        origin_hash: str | None,
    ) -> ActorBinding:
        if not session_binding_hash or not csrf_binding_hash or not origin_hash:
            raise TradingRoomError(
                "COMMAND_BINDING_REQUIRED", "server command binding is incomplete", 403
            )
        return ActorBinding(
            actor_id="operator-local-1",
            session_digest=session_binding_hash,
            csrf_token_digest=csrf_binding_hash,
            origin_hash=origin_hash,
        )

    @staticmethod
    def _port_error(exc: Exception) -> TradingRoomError:
        if isinstance(exc, CommandPortRejected):
            return TradingRoomError(exc.code, exc.message, exc.status_code)
        return TradingRoomError(
            "PRODUCTION_AUTHORITY_UNAVAILABLE",
            "normalized Agent/Risk/Paper command authority is unavailable",
            503,
        )

    def dashboard(self) -> dict[str, object]:
        portfolio = self.portfolio()
        kill = self.kill_switch()
        worker = self._worker_state()
        with psycopg.connect(self.database_url) as connection:
            market_rows = connection.execute(
                "SELECT symbol,best_bid,best_ask,quality "
                "FROM trading_room_market_reader_v1 ORDER BY symbol"
            ).fetchall()
        markets = [
            {
                "symbol": row[0],
                "best_bid": row[1],
                "best_ask": row[2],
                "quality": row[3],
            }
            for row in market_rows
        ]
        data_quality = (
            "INVALID"
            if len(markets) != 2 or any(item["quality"] == "INVALID" for item in markets)
            else "STALE"
            if any(item["quality"] == "STALE" for item in markets)
            else "HEALTHY"
        )
        orders = portfolio["orders"]
        assert isinstance(orders, list)
        ready = bool(
            worker["ready"]
            and data_quality == "HEALTHY"
            and portfolio["reconciliation_health"] == "HEALTHY"
            and portfolio["ledger_health"] == "HEALTHY"
            and kill["active"] is False
        )
        return {
            "status": "READY" if ready else "HOLD",
            "trading_mode": "paper",
            "data_quality": data_quality,
            "kill_switch": kill,
            "reconciliation": portfolio["reconciliation_health"],
            "ledger": portfolio["ledger_health"],
            "markets": markets,
            "open_order_count": sum(
                order["status"] in {"OPEN", "PARTIALLY_FILLED"}
                for order in orders
                if isinstance(order, dict)
            ),
            "paper_worker": worker,
        }

    def _worker_state(self) -> dict[str, object]:
        try:
            state = self._worker_readiness.snapshot()
        except TradingRoomError:
            raise
        except psycopg.Error as exc:
            raise TradingRoomError(
                "PAPER_WORKER_STATE_UNAVAILABLE",
                "Paper worker readiness authority is unavailable",
                503,
            ) from exc
        if (
            not isinstance(state.get("ready"), bool)
            or state.get("status") not in PAPER_WORKER_STATUSES
        ):
            raise TradingRoomError(
                "PAPER_WORKER_STATE_INVALID", "Paper worker state is invalid", 503
            )
        return state

    def create_analysis(self, symbol: str, idempotency_key: str) -> CommandResult:
        if symbol not in SYMBOL_RULES:
            raise TradingRoomError("SYMBOL_NOT_ALLOWED", "symbol is not allowed", 422)
        if not idempotency_key:
            raise TradingRoomError(
                "IDEMPOTENCY_KEY_REQUIRED", "valid idempotency key required", 400
            )
        observed_at = self._now()
        try:
            request_hash = canonical_hash(
                {
                    "method": "POST",
                    "path": "/api/v1/analysis-runs",
                    "actor": ACTOR_ID,
                    "body": {"symbol": symbol},
                }
            )
            analysis = self._agent_analysis.analyze_latest(
                symbol, observed_at, idempotency_key, request_hash
            )
            if analysis.outcome != "COMPLETED" or analysis.proposal_id is None:
                raise CommandPortRejected(
                    "ANALYSIS_HELD", "analysis did not produce a Risk-eligible Proposal", 409
                )
            if not analysis.created:
                existing_view = self.get_analysis(analysis.run_id)
                if isinstance(existing_view.get("risk_decision_id"), str):
                    return CommandResult(200, existing_view)
            risk = self._risk_evaluation.evaluate_proposal(
                analysis.proposal_id, PAPER_ACCOUNT_ID, observed_at
            )
            view = self.get_analysis(analysis.run_id)
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc
        risk_decision_id = view.get("risk_decision_id")
        if risk_decision_id != risk.decision_id:
            raise self._port_error(
                CommandPortUnavailable("analysis projection is not transactionally current")
            )
        return CommandResult(201 if analysis.created or risk.created else 200, view)

    def get_analysis(self, run_id: str) -> dict[str, object]:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT payload FROM trading_room_analysis_reader_v2 WHERE run_id=%s",
                (run_id,),
            ).fetchone()
        if row is None:
            raise TradingRoomError("ANALYSIS_NOT_FOUND", "analysis run is unavailable", 404)
        payload = row[0]
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "woozoo.analysis-run-view/v1"
        ):
            raise TradingRoomError("ANALYSIS_INVALID", "analysis projection is invalid", 503)
        return payload

    def get_risk(self, risk_id: str) -> dict[str, object]:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT decision_id,risk_input_digest,decision_hash,verdict,primary_reason,"
                "ordered_reason_codes,policy_version,proposal_hash,portfolio_snapshot_hash,"
                "data_state_hash,paper_order_preview_hash,reconciliation_checkpoint_hash,"
                "kill_switch_version,decision_as_of FROM trading_room_risk_reader_v1 "
                "WHERE decision_id=%s",
                (risk_id,),
            ).fetchone()
        if row is None:
            raise TradingRoomError("RISK_NOT_FOUND", "risk decision is unavailable", 404)
        return {
            "decision_schema_version": "woozoo.risk-decision/v1",
            "decision_id": row[0],
            "risk_input_digest": row[1],
            "decision_hash": row[2],
            "verdict": row[3],
            "primary_reason": row[4],
            "ordered_reason_codes": row[5],
            "policy_version": row[6],
            "proposal_hash": row[7],
            "portfolio_snapshot_hash": row[8],
            "data_state_hash": row[9],
            "paper_order_preview_hash": row[10],
            "reconciliation_checkpoint_hash": row[11],
            "kill_switch_version": row[12],
            "decision_as_of": row[13].isoformat().replace("+00:00", "Z"),
        }

    def approval_view(self, proposal_id: str) -> dict[str, object]:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT proposal_id,proposal_hash,status,risk_decision_id,risk_decision_hash,"
                "risk_input_digest,risk_policy_version,risk_verdict,paper_order_preview,"
                "paper_order_preview_hash,approval_id,approval_decision,approval_expires_at,"
                "revocation_id,authorization_id,authorization_attempt_outcome "
                "FROM paper_approval_view_v1 WHERE proposal_id=%s",
                (proposal_id,),
            ).fetchone()
            authorization_row = None
            if row is not None and row[14] is not None:
                authorization_row = connection.execute(
                    "SELECT status FROM paper_authorization_view_v1 WHERE authorization_id=%s",
                    (row[14],),
                ).fetchone()
        if row is None:
            raise TradingRoomError("PROPOSAL_NOT_FOUND", "proposal is unavailable", 404)
        reason_codes: list[str] = []
        if row[2] == "BLOCKED":
            reason_codes.append("AUTHORITY_BLOCKED")
        approval_status = row[11]
        if row[13] is not None:
            approval_status = "REVOKED"
        elif row[12] is not None and row[12] <= self._now():
            approval_status = "EXPIRED"
        authorization_status = None
        if authorization_row is not None:
            authorization_status = authorization_row[0]
        base_status = row[2]
        status = base_status
        if base_status == "READY":
            try:
                worker = self._worker_state()
            except TradingRoomError as exc:
                if not exc.code.startswith("PAPER_WORKER_STATE_"):
                    raise
                status = "BLOCKED"
                reason_codes.append(exc.code)
            else:
                if not worker["ready"]:
                    status = "BLOCKED"
                    reason_codes.append(
                        f"PAPER_WORKER_{worker['status']}"
                        if worker["status"] != "HEALTHY"
                        else "PAPER_WORKER_NOT_READY"
                    )
        return {
            "proposal_id": row[0],
            "proposal_hash": row[1],
            "status": status,
            "reason_codes": reason_codes,
            "risk_decision_id": row[3],
            "risk_decision_hash": row[4],
            "risk_input_digest": row[5],
            "risk_policy_version": row[6],
            "risk_verdict": row[7],
            "paper_order_preview": row[8],
            "paper_order_preview_hash": row[9],
            "approval_id": row[10],
            "approval_status": approval_status,
            "authorization_id": row[14],
            "authorization_status": authorization_status,
            "approval_action_allowed": status == "READY",
            "approve_action_allowed": status == "READY",
            "reject_action_allowed": base_status == "READY",
            "approval_ttl_seconds": 300,
            "approval_expires_at": (
                row[12].isoformat().replace("+00:00", "Z") if row[12] is not None else None
            ),
            "view_version": 1,
            "served_at": self._now().isoformat().replace("+00:00", "Z"),
        }

    def decide_approval(
        self,
        *,
        proposal_id: str,
        decision: Literal["APPROVE", "REJECT"],
        expected_version: int,
        preview_hash: str,
        idempotency_key: str,
        reason: str,
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        if decision == "APPROVE":
            worker = self._worker_state()
            if not worker["ready"]:
                raise TradingRoomError(
                    "PAPER_WORKER_NOT_READY",
                    "Paper authorization worker is not ready",
                    409,
                )
        body = {
            "proposal_id": proposal_id,
            "decision": decision,
            "expected_version": expected_version,
            "paper_order_preview_hash": preview_hash,
            "reason": reason,
        }
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": "/api/v1/paper-approvals",
                "actor": ACTOR_ID,
                "body": body,
            }
        )
        try:
            persisted = self._risk_commands.decide_approval(
                ApprovalDecisionCommand(
                    proposal_id=proposal_id,
                    decision=decision,
                    expected_version=expected_version,
                    paper_order_preview_hash=preview_hash,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    reason=reason,
                    binding=self._binding(session_binding_hash, csrf_binding_hash, origin_hash),
                    decided_at=self._now(),
                )
            )
            response = persisted.response
            if response.get("result") == "REJECTED":
                return CommandResult(201 if persisted.created else 200, response)
            authorization = response.get("authorization")
            if not isinstance(authorization, dict):
                raise CommandPortUnavailable("Risk authority omitted authorization receipt")
            authorization_id = authorization.get("authorization_id")
            if not isinstance(authorization_id, str):
                raise CommandPortUnavailable("Risk authority returned invalid authorization")
            if self._after_authorization_issued is not None:
                self._after_authorization_issued(authorization_id)
            issued_response = dict(response)
            issued_response["result"] = "AUTHORIZATION_ISSUED"
            return CommandResult(
                201 if persisted.created else 200,
                issued_response,
            )
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc

    def attempt_authorization(self, authorization_id: str) -> CommandResult:
        """Internal worker entry point; this is intentionally not a browser route."""
        try:
            result = self._paper_commands.attempt_authorization(authorization_id)
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc
        return CommandResult(201 if result.created else 200, result.response)

    def revoke(
        self,
        approval_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": f"/api/v1/paper-approvals/{approval_id}/revocations",
                "actor": ACTOR_ID,
                "body": {"expected_version": expected_version, "reason": reason},
            }
        )
        try:
            result = self._risk_commands.revoke_approval(
                ApprovalRevocationCommand(
                    approval_id=approval_id,
                    expected_version=expected_version,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    reason=reason,
                    binding=self._binding(session_binding_hash, csrf_binding_hash, origin_hash),
                    revoked_at=self._now(),
                )
            )
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc
        return CommandResult(201 if result.created else 200, result.response)

    def cancel_order(
        self,
        order_id: str,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": f"/api/v1/paper-orders/{order_id}/cancel",
                "actor": ACTOR_ID,
                "body": {"expected_version": expected_version, "reason": reason},
            }
        )
        try:
            result = self._paper_commands.cancel_order(
                PaperCancellationCommand(
                    order_id=order_id,
                    expected_version=expected_version,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    reason=reason,
                    binding=self._binding(session_binding_hash, csrf_binding_hash, origin_hash),
                )
            )
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc
        return CommandResult(201 if result.created else 200, result.response)

    def portfolio(self) -> dict[str, object]:
        with psycopg.connect(self.database_url) as connection:
            balances = connection.execute(
                "SELECT asset,available,held,version FROM paper_portfolio_reader_v1 ORDER BY asset"
            ).fetchall()
            orders = connection.execute(
                "SELECT order_id,client_order_id,authorization_id,approval_id,proposal_hash,"
                "risk_decision_hash,paper_order_preview_hash,symbol,side,order_type,"
                "time_in_force,quantity,limit_price,filled_quantity,status,version "
                "FROM paper_order_reader_v1 ORDER BY order_id"
            ).fetchall()
            entries = connection.execute(
                "SELECT transaction_id,line_no,account_code,commodity,debit,credit "
                "FROM paper_ledger_entry_reader_v1 ORDER BY transaction_id,line_no"
            ).fetchall()
            health = connection.execute(
                "SELECT reconciliation_status,checkpoint_id,checkpoint_created_at,"
                "checkpoint_input_digest,ledger_status,ledger_imbalance_count,"
                "checkpoint_authority_sequence,current_authority_sequence "
                "FROM paper_health_reader_v1 WHERE account_id=%s",
                (PAPER_ACCOUNT_ID,),
            ).fetchone()
        if health is None:
            raise TradingRoomError(
                "PORTFOLIO_HEALTH_MISSING",
                "authoritative Paper reconciliation and ledger health is unavailable",
                503,
            )
        reconciliation_status = health[0]
        ledger_status = health[4]
        ledger_imbalance_count = health[5]
        if reconciliation_status not in {"HEALTHY", "FAILED", "MISSING", "STALE"}:
            raise TradingRoomError(
                "RECONCILIATION_HEALTH_INVALID",
                "authoritative reconciliation health is invalid",
                503,
            )
        if (
            ledger_status not in {"BALANCED", "UNBALANCED"}
            or not isinstance(ledger_imbalance_count, int)
            or ledger_imbalance_count < 0
            or (ledger_status == "BALANCED") != (ledger_imbalance_count == 0)
        ):
            raise TradingRoomError(
                "LEDGER_HEALTH_INVALID", "authoritative ledger health is invalid", 503
            )
        by_asset = {row[0]: row for row in balances}
        usdt = by_asset.get("USDT", ("USDT", Decimal(0), Decimal(0), 0))
        order_values = [
            {
                "order_id": row[0],
                "client_order_id": row[1],
                "authorization_id": row[2],
                "approval_id": row[3],
                "proposal_hash": row[4],
                "risk_decision_hash": row[5],
                "paper_order_preview_hash": row[6],
                "symbol": row[7],
                "side": row[8],
                "order_type": row[9],
                "time_in_force": row[10],
                "quantity": decimal_text(row[11]),
                "limit_price": decimal_text(row[12]),
                "filled_quantity": decimal_text(row[13]),
                "status": row[14],
                "version": row[15],
            }
            for row in orders
        ]
        ledger_values: list[dict[str, object]] = []
        for row in entries:
            if row[4] > 0:
                side, amount = "DEBIT", row[4]
            else:
                side, amount = "CREDIT", row[5]
            ledger_values.append(
                {
                    "journal_id": row[0],
                    "line_no": row[1],
                    "account": row[2],
                    "asset": row[3],
                    "side": side,
                    "amount": decimal_text(amount),
                }
            )
        return {
            "namespace": "paper",
            "portfolio_version": max((row[3] for row in balances), default=0),
            "ledger_version": len({row[0] for row in entries}),
            "available_quote": decimal_text(usdt[1]),
            "held_quote": decimal_text(usdt[2]),
            "positions": {
                symbol: {
                    "available": decimal_text(by_asset.get(asset, (asset, Decimal(0), 0, 0))[1]),
                    "held": decimal_text(by_asset.get(asset, (asset, 0, Decimal(0), 0))[2]),
                }
                for symbol, asset in (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"))
            },
            "orders": order_values,
            "ledger_entries": ledger_values,
            "ledger_health": "HEALTHY" if ledger_status == "BALANCED" else "FAILED",
            "reconciliation_health": (
                "HEALTHY" if reconciliation_status == "HEALTHY" else "FAILED"
            ),
            "ledger_checkpoint_id": health[1],
            "reconciliation_as_of": (
                health[2].isoformat().replace("+00:00", "Z") if health[2] is not None else None
            ),
            "reconciliation_input_digest": health[3],
            "reconciliation_status": reconciliation_status,
            "checkpoint_authority_sequence": health[6],
            "current_authority_sequence": health[7],
            "ledger_status": ledger_status,
            "ledger_imbalance_count": ledger_imbalance_count,
        }

    def audit_events(self, after: int = 0) -> list[dict[str, object]]:
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT sequence,event_id,event_type,occurred_at,producer,actor_id,data "
                "FROM trading_room_audit_reader_v1 WHERE sequence>%s ORDER BY sequence",
                (after,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_id": row[1],
                "event_type": row[2],
                "producer": row[4],
                "actor_id": row[5],
                "occurred_at": row[3].isoformat().replace("+00:00", "Z"),
                "data": browser_safe_audit_value(row[6]),
            }
            for row in rows
        ]

    def kill_switch(self) -> dict[str, object]:
        worker = self._worker_state()
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT active,version,last_activation_event_id,last_recovery_event_id,"
                "cancellation_status,cancellation_completed_at,open_order_count,data_status,"
                "reconciliation_status,ledger_status,recovery_allowed "
                "FROM kill_switch_recovery_reader_v1 "
                "WHERE scope='paper-global'"
            ).fetchone()
        if row is None:
            raise TradingRoomError("KILL_BARRIER_MISSING", "Kill barrier is unavailable", 503)
        return {
            "active": row[0],
            "version": row[1],
            "activation_event_id": row[2],
            "recovery_event_id": row[3],
            "cancellation_status": row[4],
            "cancellation_completed_at": (
                row[5].isoformat().replace("+00:00", "Z") if row[5] is not None else None
            ),
            "open_order_count": row[6],
            "data_status": row[7],
            "reconciliation_status": row[8],
            "ledger_status": row[9],
            "recovery_allowed": row[10],
            "paper_worker": worker,
        }

    def activate_kill(
        self,
        expected_version: int,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": "/api/v1/kill-switch/activate",
                "actor": ACTOR_ID,
                "body": {"expected_version": expected_version, "reason": reason},
            }
        )
        try:
            activated = self._risk_commands.activate_kill(
                KillActivationCommand(
                    expected_version=expected_version,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    reason=reason,
                    binding=self._binding(session_binding_hash, csrf_binding_hash, origin_hash),
                    observed_at=self._now(),
                )
            )
            activation_event_id = activated.response.get("activation_event_id")
            payload_hash = activated.response.get("payload_hash")
            if not isinstance(activation_event_id, str) or not isinstance(payload_hash, str):
                raise CommandPortUnavailable("Risk authority returned invalid Kill receipt")
            cancelled = self._paper_commands.consume_kill_activation(
                activation_event_id, payload_hash, self._now()
            )
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc
        kill_switch = activated.response.get("kill_switch")
        if not isinstance(kill_switch, dict):
            raise self._port_error(CommandPortUnavailable("Risk Kill receipt is invalid"))
        response: dict[str, object] = {
            "result": activated.response.get("result", "ACTIVATED"),
            "kill_switch": kill_switch,
            "cancelled_order_ids": list(cancelled),
        }
        return CommandResult(201 if activated.created else 200, response)

    def recover_kill(
        self,
        expected_version: int,
        activation_event_id: str,
        incident_reference: str,
        reason: str,
        *,
        idempotency_key: str = "",
        session_binding_hash: str | None = None,
        csrf_binding_hash: str | None = None,
        origin_hash: str | None = None,
    ) -> CommandResult:
        request_hash = canonical_hash(
            {
                "method": "POST",
                "path": "/api/v1/kill-switch/recover",
                "actor": ACTOR_ID,
                "body": {
                    "expected_version": expected_version,
                    "activation_event_id": activation_event_id,
                    "incident_reference": incident_reference,
                    "reason": reason,
                },
            }
        )
        try:
            result = self._risk_commands.recover_kill(
                KillRecoveryCommand(
                    expected_version=expected_version,
                    activation_event_id=activation_event_id,
                    incident_reference=incident_reference,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    reason=reason,
                    binding=self._binding(session_binding_hash, csrf_binding_hash, origin_hash),
                    observed_at=self._now(),
                )
            )
        except (CommandPortRejected, CommandPortUnavailable) as exc:
            raise self._port_error(exc) from exc
        return CommandResult(201 if result.created else 200, result.response)
