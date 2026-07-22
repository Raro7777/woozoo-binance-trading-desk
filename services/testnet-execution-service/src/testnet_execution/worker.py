"""Fail-closed long-running Phase 8 execution worker entry point."""

from __future__ import annotations

import argparse
import os
import signal
import time

from .materializer import PostgresTestnetRiskMaterializer
from .persistence import ExecutionRuntimeBinding
from .reconciliation import PostgresReconciliationWorker
from .validated_intents import ValidatingPostgresIntentWorker


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def runtime_binding_from_environment() -> ExecutionRuntimeBinding:
    return ExecutionRuntimeBinding(
        gateway_instance_id=_required("SPOT_TESTNET_GATEWAY_INSTANCE_ID"),
        build_digest=_required("SPOT_TESTNET_GATEWAY_BUILD_DIGEST"),
        configuration_digest=_required("SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST"),
        allowlist_digest=_required("SPOT_TESTNET_ALLOWLIST_DIGEST"),
    )


def run_execution_cycle(
    database_url: str,
    binding: ExecutionRuntimeBinding,
) -> dict[str, object] | None:
    """Run the production authority ordering exactly once for tests and the daemon."""

    result = ValidatingPostgresIntentWorker(database_url, binding).run_once()
    if result is None:
        result = PostgresReconciliationWorker(database_url).run_once()
    if result is None:
        result = PostgresTestnetRiskMaterializer(database_url).run_once()
    return result


def _poll_seconds() -> float:
    raw = os.environ.get("TESTNET_EXECUTION_POLL_SECONDS", "0.25").strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise SystemExit("TESTNET_EXECUTION_POLL_SECONDS must be numeric") from error
    if value <= 0 or value > 5:
        raise SystemExit("TESTNET_EXECUTION_POLL_SECONDS is outside the bounded range")
    return value


def _run_loop(database_url: str, binding: ExecutionRuntimeBinding) -> int:
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    base_delay = _poll_seconds()
    failures = 0
    while not stopping:
        try:
            result = run_execution_cycle(database_url, binding)
            failures = 0
            delay = base_delay if result is None else min(base_delay, 0.05)
        except Exception:
            failures += 1
            delay = min(base_delay * (2 ** min(failures, 5)), 30.0)
        if not stopping:
            time.sleep(delay)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("TRADING_MODE") != "paper":
        raise SystemExit("TRADING_MODE must be explicitly paper")
    if args.check_config:
        return 0
    binding = runtime_binding_from_environment()
    database_url = _required("TESTNET_EXECUTION_DATABASE_URL")
    if args.once:
        return 0 if run_execution_cycle(database_url, binding) is not None else 2
    return _run_loop(database_url, binding)


if __name__ == "__main__":
    raise SystemExit(main())
