"""Phase 7 browser routes; internal authorization/order capabilities stay private."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Annotated, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .security import (
    ACTOR_ID,
    COOKIE_NAME,
    AuthenticationFailed,
    CommandGuardRejected,
    LocalOperatorSecurity,
    PostgresSecurityRepository,
    SessionRecord,
    SessionRejected,
    token_digest,
)
from .trading_room import CommandResult, TradingRoom, TradingRoomError


IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginCommand(ClosedModel):
    password: str = Field(min_length=1, max_length=1024)


class AnalysisCommand(ClosedModel):
    symbol: Literal["BTCUSDT", "ETHUSDT"]


class ApprovalCommand(ClosedModel):
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["APPROVE", "REJECT"]
    expected_version: int = Field(ge=1)
    paper_order_preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=512)


class RevocationCommand(ClosedModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


class CancellationCommand(ClosedModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


class KillActivationCommand(ClosedModel):
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=512)


class KillRecoveryCommand(ClosedModel):
    expected_version: int = Field(ge=1)
    activation_event_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    incident_reference: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


def load_local_security(environment: Mapping[str, str]) -> LocalOperatorSecurity | None:
    verifier_file = environment.get("LOCAL_OPERATOR_VERIFIER_FILE", "").strip()
    origin = environment.get("LOCAL_OPERATOR_ORIGIN", "https://localhost:3443").strip()
    if not verifier_file:
        return None
    database_url = environment.get("CONTROL_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("CONTROL_DATABASE_URL is required when local auth is enabled")
    path = Path(verifier_file)
    try:
        verifier = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("local operator verifier file is unavailable") from exc
    repository = PostgresSecurityRepository(database_url, verifier)
    return LocalOperatorSecurity(verifier, origin, repository=repository)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _result(result: CommandResult) -> JSONResponse:
    return JSONResponse(result.body, status_code=result.status_code)


def register_trading_room_routes(
    app: FastAPI, security: LocalOperatorSecurity, room: TradingRoom
) -> None:
    def raw_session(request: Request) -> str | None:
        return request.cookies.get(COOKIE_NAME)

    def require_session(request: Request) -> JSONResponse | None:
        try:
            security.authenticate(raw_session(request))
        except SessionRejected:
            return _error("SESSION_REQUIRED", "operator session required", 401)
        return None

    def require_command(request: Request, csrf: str | None) -> SessionRecord | JSONResponse:
        try:
            return security.consume_command_guard(
                raw_session(request), csrf, request.headers.get("origin")
            )
        except (SessionRejected, CommandGuardRejected):
            return _error("COMMAND_GUARD_REJECTED", "command guard rejected", 403)

    def require_idempotency(value: str | None) -> JSONResponse | None:
        if value is None or IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
            return _error("IDEMPOTENCY_KEY_REQUIRED", "valid Idempotency-Key required", 400)
        return None

    def invoke(action: object) -> JSONResponse:
        try:
            assert callable(action)
            return _result(action())
        except TradingRoomError as exc:
            return _error(exc.code, exc.message, exc.status_code)

    def approval_receipt(result: CommandResult) -> JSONResponse:
        approval = result.body.get("approval")
        authorization = result.body.get("authorization")
        order = result.body.get("order")
        assert isinstance(approval, dict)
        body: dict[str, object] = {
            "result": result.body["result"],
            "approval_id": approval["approval_id"],
            "approval_status": approval["decision"],
            "authorization_status": None,
            "order_id": None,
        }
        if isinstance(authorization, dict):
            if result.body["result"] == "AUTHORIZATION_ISSUED":
                body["authorization_status"] = "ISSUED"
            else:
                body["authorization_status"] = (
                    "CONSUMED" if result.body["result"] == "CONSUMED_ORDER_CREATED" else "BLOCKED"
                )
        if isinstance(order, dict):
            body["order_id"] = order["order_id"]
        return JSONResponse(body, status_code=result.status_code)

    def require_if_match(value: str | None, expected_version: int) -> JSONResponse | None:
        if value is None or value.strip().removeprefix("W/").strip('"') != str(expected_version):
            return _error("PRECONDITION_FAILED", "If-Match must bind expected_version", 412)
        return None

    @app.post("/api/v1/session/login")
    def login(command: LoginCommand, request: Request) -> JSONResponse:
        if request.url.scheme != "https":
            return _error("HTTPS_REQUIRED", "login requires HTTPS", 400)
        try:
            raw = security.login(
                command.password,
                request.headers.get("origin"),
                request.cookies.get(COOKIE_NAME),
            )
        except (AuthenticationFailed, CommandGuardRejected):
            return _error("AUTHENTICATION_FAILED", "authentication failed", 401)
        response = JSONResponse({"actor_id": ACTOR_ID, "authenticated": True})
        response.set_cookie(
            COOKIE_NAME,
            raw,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=8 * 60 * 60,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/session")
    def session(request: Request) -> JSONResponse:
        try:
            csrf, session_record, csrf_record = security.issue_csrf_bundle(raw_session(request))
        except SessionRejected:
            return _error("SESSION_REQUIRED", "operator session required", 401)
        response = JSONResponse(
            {
                "actor_id": ACTOR_ID,
                "issued_at": session_record.issued_at.isoformat().replace("+00:00", "Z"),
                "idle_expires_at": session_record.idle_expires_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "absolute_expires_at": session_record.absolute_expires_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "csrf_token": csrf,
                "csrf_expires_at": csrf_record.expires_at.isoformat().replace("+00:00", "Z"),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/v1/session/logout")
    def logout(request: Request, x_csrf_token: str | None = Header(default=None)) -> JSONResponse:
        try:
            security.logout(raw_session(request), x_csrf_token, request.headers.get("origin"))
        except (SessionRejected, CommandGuardRejected):
            return _error("COMMAND_GUARD_REJECTED", "command guard rejected", 403)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/trading-room")
    def dashboard(request: Request) -> JSONResponse:
        denied = require_session(request)
        return denied or JSONResponse(room.dashboard())

    @app.post("/api/v1/analysis-runs")
    def create_analysis(
        command: AnalysisCommand,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> JSONResponse:
        invalid_key = require_idempotency(idempotency_key)
        if invalid_key:
            return invalid_key
        guarded = require_command(request, x_csrf_token)
        if isinstance(guarded, JSONResponse):
            return guarded
        return invoke(lambda: room.create_analysis(command.symbol, idempotency_key or ""))

    @app.get("/api/v1/analysis-runs/{run_id}")
    def get_analysis(run_id: str, request: Request) -> JSONResponse:
        denied = require_session(request)
        if denied:
            return denied
        try:
            return JSONResponse(room.get_analysis(run_id))
        except TradingRoomError as exc:
            return _error(exc.code, exc.message, exc.status_code)

    @app.get("/api/v1/risk-decisions/{risk_id}")
    def get_risk(risk_id: str, request: Request) -> JSONResponse:
        denied = require_session(request)
        if denied:
            return denied
        try:
            return JSONResponse(room.get_risk(risk_id))
        except TradingRoomError as exc:
            return _error(exc.code, exc.message, exc.status_code)

    @app.get("/api/v1/proposals/{proposal_id}/approval-view")
    def approval_view(proposal_id: str, request: Request) -> JSONResponse:
        denied = require_session(request)
        if denied:
            return denied
        try:
            return JSONResponse(room.approval_view(proposal_id))
        except TradingRoomError as exc:
            return _error(exc.code, exc.message, exc.status_code)

    @app.post("/api/v1/paper-approvals")
    def approve(
        command: ApprovalCommand,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        if_match: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> JSONResponse:
        invalid_key = require_idempotency(idempotency_key)
        if invalid_key:
            return invalid_key
        precondition = require_if_match(if_match, command.expected_version)
        if precondition:
            return precondition
        guarded = require_command(request, x_csrf_token)
        if isinstance(guarded, JSONResponse):
            return guarded
        try:
            result = room.decide_approval(
                proposal_id=command.proposal_id,
                decision=command.decision,
                expected_version=command.expected_version,
                preview_hash=command.paper_order_preview_hash,
                idempotency_key=idempotency_key or "",
                reason=command.reason,
                session_binding_hash=guarded.digest,
                csrf_binding_hash=token_digest(x_csrf_token or ""),
                origin_hash=token_digest(request.headers.get("origin") or ""),
            )
            return approval_receipt(result)
        except TradingRoomError as exc:
            return _error(exc.code, exc.message, exc.status_code)

    @app.post("/api/v1/paper-approvals/{approval_id}/revocations")
    def revoke(
        approval_id: str,
        command: RevocationCommand,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        if_match: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> JSONResponse:
        invalid_key = require_idempotency(idempotency_key)
        if invalid_key:
            return invalid_key
        precondition = require_if_match(if_match, command.expected_version)
        if precondition:
            return precondition
        guarded = require_command(request, x_csrf_token)
        if isinstance(guarded, JSONResponse):
            return guarded
        return invoke(
            lambda: room.revoke(
                approval_id,
                command.expected_version,
                command.reason,
                idempotency_key=idempotency_key or "",
                session_binding_hash=guarded.digest,
                csrf_binding_hash=token_digest(x_csrf_token or ""),
                origin_hash=token_digest(request.headers.get("origin") or ""),
            )
        )

    @app.get("/api/v1/paper-orders/{order_id}")
    def get_order(order_id: str, request: Request) -> JSONResponse:
        denied = require_session(request)
        if denied:
            return denied
        portfolio_view = room.portfolio()
        order_values = portfolio_view["orders"]
        assert isinstance(order_values, list)
        orders: dict[str, dict[str, object]] = {}
        for item in order_values:
            assert isinstance(item, dict)
            orders[str(item["order_id"])] = item
        order = orders.get(order_id)
        return (
            JSONResponse(order)
            if order
            else _error("ORDER_NOT_FOUND", "Paper order is unavailable", 404)
        )

    @app.post("/api/v1/paper-orders/{order_id}/cancel")
    def cancel_order(
        order_id: str,
        command: CancellationCommand,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        if_match: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> JSONResponse:
        invalid_key = require_idempotency(idempotency_key)
        if invalid_key:
            return invalid_key
        precondition = require_if_match(if_match, command.expected_version)
        if precondition:
            return precondition
        guarded = require_command(request, x_csrf_token)
        if isinstance(guarded, JSONResponse):
            return guarded
        return invoke(
            lambda: room.cancel_order(
                order_id,
                command.expected_version,
                command.reason,
                idempotency_key=idempotency_key or "",
                session_binding_hash=guarded.digest,
                csrf_binding_hash=token_digest(x_csrf_token or ""),
                origin_hash=token_digest(request.headers.get("origin") or ""),
            )
        )

    @app.get("/api/v1/paper-portfolio")
    def portfolio(request: Request) -> JSONResponse:
        denied = require_session(request)
        return denied or JSONResponse(room.portfolio())

    @app.get("/api/v1/audit-events")
    def audit_events(request: Request, after: Annotated[int, Query(ge=0)] = 0) -> JSONResponse:
        denied = require_session(request)
        if denied:
            return denied
        events = room.audit_events(after)
        next_cursor = events[-1]["sequence"] if events else after
        return JSONResponse({"events": events, "next_cursor": next_cursor})

    @app.get("/api/v1/kill-switch")
    def get_kill(request: Request) -> JSONResponse:
        denied = require_session(request)
        return denied or JSONResponse(room.kill_switch())

    @app.post("/api/v1/kill-switch/activate")
    def activate_kill(
        command: KillActivationCommand,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        if_match: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> JSONResponse:
        invalid_key = require_idempotency(idempotency_key)
        if invalid_key:
            return invalid_key
        precondition = require_if_match(if_match, command.expected_version)
        if precondition:
            return precondition
        guarded = require_command(request, x_csrf_token)
        if isinstance(guarded, JSONResponse):
            return guarded
        return invoke(
            lambda: room.activate_kill(
                command.expected_version,
                command.reason,
                idempotency_key=idempotency_key or "",
                session_binding_hash=guarded.digest,
                csrf_binding_hash=token_digest(x_csrf_token or ""),
                origin_hash=token_digest(request.headers.get("origin") or ""),
            )
        )

    @app.post("/api/v1/kill-switch/recover")
    def recover_kill(
        command: KillRecoveryCommand,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        if_match: str | None = Header(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> JSONResponse:
        invalid_key = require_idempotency(idempotency_key)
        if invalid_key:
            return invalid_key
        precondition = require_if_match(if_match, command.expected_version)
        if precondition:
            return precondition
        guarded = require_command(request, x_csrf_token)
        if isinstance(guarded, JSONResponse):
            return guarded
        return invoke(
            lambda: room.recover_kill(
                command.expected_version,
                command.activation_event_id,
                command.incident_reference,
                command.reason,
                idempotency_key=idempotency_key or "",
                session_binding_hash=guarded.digest,
                csrf_binding_hash=token_digest(x_csrf_token or ""),
                origin_hash=token_digest(request.headers.get("origin") or ""),
            )
        )
