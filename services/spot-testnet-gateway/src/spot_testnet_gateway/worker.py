"""Fail-closed long-running Spot Testnet Gateway process entry point."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Callable
import os
import signal
import time
from typing import Protocol

from .capabilities import (
    Capability,
    GatewayRequest,
    existing_order_request,
    reconciliation_request,
    submit_limit_gtc_request,
)
from .dispatch import GatewayRuntimeBinding
from .persistence import PostgresGatewayDispatcher, PostgresGatewayObservationWriter
from .settings import GatewaySettings
from .supervisor import (
    MAX_CLOCK_LAG_MS,
    MAX_CLOCK_LEAD_MS,
    ReconciliationSupervisor,
    UserDataObservationPump,
)
from .transport import (
    SanitizedTransportResult,
    SignedRestTransport,
    UserDataWebSocketTransport,
)


class RestTransport(Protocol):
    def send(self, request: GatewayRequest) -> SanitizedTransportResult: ...


class UserDataTransport(Protocol):
    def events(self, *, request_id: str, timestamp_ms: str) -> AsyncIterator[dict[str, object]]: ...


def _runtime(settings: GatewaySettings) -> GatewayRuntimeBinding:
    return GatewayRuntimeBinding(
        gateway_instance_id=str(settings.gateway_instance_id),
        build_digest=str(settings.build_digest),
        configuration_digest=str(settings.configuration_digest),
        allowlist_digest=str(settings.allowlist_digest),
    )


def _positive_interval(name: str, default: float, *, maximum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise SystemExit(f"{name} must be numeric") from error
    if value <= 0 or value > maximum:
        raise SystemExit(f"{name} is outside the bounded range")
    return value


def _command_sender(
    settings: GatewaySettings,
    database_url: str,
    transport: RestTransport,
) -> Callable[[dict[str, object]], SanitizedTransportResult]:
    writer = PostgresGatewayObservationWriter(database_url, runtime=_runtime(settings))

    def send(payload: dict[str, object]) -> SanitizedTransportResult:
        # The authority is re-read directly before each transport effect. This
        # closes the former authority-check -> dispatch window in the worker.
        writer.assert_authorized()
        local_before_ms = int(time.time() * 1000)
        time_result = transport.send(GatewayRequest(Capability.TIME, "GET", "/api/v3/time", ()))
        local_after_ms = int(time.time() * 1000)
        time_observation = time_result.observation
        server_time_value = (
            time_observation.get("server_time_ms") if time_observation is not None else None
        )
        if (
            time_result.status != "EXCHANGE_ACKNOWLEDGED"
            or time_observation is None
            or not isinstance(server_time_value, int)
            or isinstance(server_time_value, bool)
            or server_time_value > local_after_ms + MAX_CLOCK_LEAD_MS
            or server_time_value < local_before_ms - MAX_CLOCK_LAG_MS
        ):
            raise ValueError("SERVER_TIME_OFFSET_UNSAFE")
        writer.record(time_observation, source_channel="REST")
        timestamp_ms = str(server_time_value)
        command_type = str(payload.get("command_type", ""))
        if command_type == "SUBMIT_LIMIT_ORDER":
            request = submit_limit_gtc_request(
                symbol=str(payload["symbol"]),
                side=str(payload["side"]),
                quantity=str(payload["quantity"]),
                price=str(payload["limit_price"]),
                client_order_id=str(payload["client_order_id"]),
                timestamp_ms=timestamp_ms,
            )
        elif command_type in {"CANCEL_EXISTING_ORDER", "QUERY_EXISTING_ORDER"}:
            capability = (
                Capability.CANCEL_BY_CLIENT_ID
                if command_type == "CANCEL_EXISTING_ORDER"
                else Capability.QUERY_BY_CLIENT_ID
            )
            request = existing_order_request(
                capability=capability,
                symbol=str(payload["symbol"]),
                client_order_id=str(payload["client_order_id"]),
                timestamp_ms=timestamp_ms,
            )
        elif command_type == "RECONCILIATION_OBSERVATION":
            try:
                capability = Capability(str(payload["capability_id"]))
            except ValueError as error:
                raise ValueError("RECONCILIATION_CAPABILITY_MISMATCH") from error
            request = reconciliation_request(
                capability=capability,
                symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
                order_id=(
                    str(payload["exchange_order_id"])
                    if payload.get("exchange_order_id") is not None
                    else None
                ),
                timestamp_ms=timestamp_ms,
            )
        else:
            raise ValueError("GATEWAY_COMMAND_TYPE_NOT_ALLOWED")
        writer.assert_authorized()
        return transport.send(request)

    return send


def run_gateway_command_cycle(
    settings: GatewaySettings,
    database_url: str,
    *,
    transport: RestTransport | None = None,
) -> dict[str, object] | None:
    selected = transport or SignedRestTransport(settings)
    return PostgresGatewayDispatcher(database_url, runtime=_runtime(settings)).run_once(
        _command_sender(settings, database_url, selected)
    )


def run_gateway_reconciliation_cycle(
    settings: GatewaySettings,
    database_url: str,
    *,
    transport: RestTransport | None = None,
) -> dict[str, object]:
    selected = transport or SignedRestTransport(settings)
    writer = PostgresGatewayObservationWriter(database_url, runtime=_runtime(settings))
    return {"observation_digests": ReconciliationSupervisor(selected, writer).run_once()}


async def run_gateway_user_data_session(
    settings: GatewaySettings,
    database_url: str,
    *,
    transport: UserDataTransport | None = None,
) -> dict[str, object]:
    writer = PostgresGatewayObservationWriter(database_url, runtime=_runtime(settings))
    selected = transport or UserDataWebSocketTransport(settings)
    request_id = f"wz8-user-data-{str(settings.gateway_instance_id)[:16]}"
    async for _ in UserDataObservationPump(selected, writer).events(request_id=request_id):
        pass
    return {"status": "USER_DATA_STREAM_ENDED"}


def _run_loop(cycle: Callable[[], object | None], *, idle_seconds: float) -> int:
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    failures = 0
    while not stopping:
        try:
            result = cycle()
            failures = 0
            delay = idle_seconds if result is None else min(idle_seconds, 0.05)
        except Exception:
            failures += 1
            delay = min(idle_seconds * (2 ** min(failures, 5)), 30.0)
        if not stopping:
            time.sleep(delay)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    settings = GatewaySettings.from_mapping(os.environ)
    if args.check_config:
        return 0
    if not settings.deployment_enabled:
        raise SystemExit("Spot Testnet Gateway is disabled")
    database_url = os.environ.get("SPOT_TESTNET_GATEWAY_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("SPOT_TESTNET_GATEWAY_DATABASE_URL is required")
    run_mode = os.environ.get("SPOT_TESTNET_GATEWAY_RUN_MODE", "").strip()
    if run_mode == "command":

        def cycle() -> object | None:
            return run_gateway_command_cycle(settings, database_url)

        interval = _positive_interval("SPOT_TESTNET_GATEWAY_POLL_SECONDS", 0.25, maximum=5.0)
    elif run_mode == "reconciliation":

        def cycle() -> object | None:
            return run_gateway_reconciliation_cycle(settings, database_url)

        interval = _positive_interval("SPOT_TESTNET_RECONCILIATION_SECONDS", 15.0, maximum=60.0)
    elif run_mode == "user-data":

        def cycle() -> object | None:
            return asyncio.run(run_gateway_user_data_session(settings, database_url))

        interval = _positive_interval("SPOT_TESTNET_USER_DATA_BACKOFF_SECONDS", 1.0, maximum=30.0)
    else:
        raise SystemExit(
            "SPOT_TESTNET_GATEWAY_RUN_MODE must be command, reconciliation, or user-data"
        )
    if args.once:
        return 0 if cycle() is not None else 2
    return _run_loop(cycle, idle_seconds=interval)


if __name__ == "__main__":
    raise SystemExit(main())
