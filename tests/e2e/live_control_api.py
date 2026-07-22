"""Loopback-only Phase 7 control API and Paper worker acceptance harness.

The HTTPS server owns disposable infrastructure setup.  This process uses the
same least-privilege database identities as the application and deliberately
does not expose a browser route for the Paper authorization worker.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import uvicorn
from fastapi import FastAPI

from control_api.app import create_app
from paper_engine.persistence import Phase7AuthorizationWorker, PostgresPaperStore


PAPER_ACCOUNT_ID = "c71f45a74649ecfbc2f897ed1ced77309accd4dbbc069c9cd425754204c09b3e"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required by the Phase 7 E2E harness")
    return value


def run_worker_once() -> int:
    """Attempt exactly one pending authorization outside browser capability."""

    database_url = _required_environment("PAPER_DATABASE_URL")
    result = Phase7AuthorizationWorker(PostgresPaperStore(database_url)).run_once()
    if result is None:
        return 2
    return 0


def run_reconciliation_once() -> int:
    """Record a fresh Paper checkpoint after an authoritative worker effect."""

    database_url = _required_environment("PAPER_DATABASE_URL")
    result = PostgresPaperStore(database_url).reconcile(
        PAPER_ACCOUNT_ID,
        checkpoint_id=f"e2e-reconciliation-{uuid4()}",
        created_at=datetime.now(UTC),
    )
    if result.status != "HEALTHY":
        raise RuntimeError("E2E_RECONCILIATION_FAILED:" + ",".join(result.mismatch_codes))
    return 0


def _testnet_runtime_binding():
    from spot_testnet_gateway.dispatch import GatewayRuntimeBinding

    return GatewayRuntimeBinding(
        gateway_instance_id=_required_environment("SPOT_TESTNET_GATEWAY_INSTANCE_ID"),
        build_digest=_required_environment("SPOT_TESTNET_GATEWAY_BUILD_DIGEST"),
        configuration_digest=_required_environment("SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST"),
        allowlist_digest=_required_environment("SPOT_TESTNET_ALLOWLIST_DIGEST"),
    )


def run_testnet_execution_once() -> int:
    """Run one real execution authority cycle in its own process."""
    from testnet_execution.persistence import ExecutionRuntimeBinding
    from testnet_execution.worker import run_execution_cycle

    database_url = _required_environment("TESTNET_EXECUTION_DATABASE_URL")
    gateway = _testnet_runtime_binding()
    binding = ExecutionRuntimeBinding(
        gateway_instance_id=gateway.gateway_instance_id,
        build_digest=gateway.build_digest,
        configuration_digest=gateway.configuration_digest,
        allowlist_digest=gateway.allowlist_digest,
    )
    result = run_execution_cycle(database_url, binding)
    if result is None:
        return 2
    print("P8_EXECUTION_RESULT:" + json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


class _PinnedFakeTestnetTransport:
    """Deterministic exchange substitute with an explicit zero-network counter."""

    def __init__(self, order: dict[str, object] | None = None) -> None:
        self.order = order
        self.fake_transport_calls = 0
        self.external_exchange_network_calls = 0

    def send(self, request):
        from spot_testnet_gateway.capabilities import Capability, validate_request
        from spot_testnet_gateway.transport import SanitizedTransportResult

        validate_request(request)
        self.fake_transport_calls += 1
        observed_ms = int(datetime.now(UTC).timestamp() * 1000)
        if request.capability is Capability.TIME:
            observation = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "SERVER_TIME",
                "capability_id": "SPOT_TESTNET_TIME",
                "authoritative": True,
                "server_time_ms": observed_ms,
                "event_time_ms": observed_ms,
            }
        elif request.capability is Capability.ACCOUNT:
            observation = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "ACCOUNT_SNAPSHOT",
                "capability_id": "SPOT_TESTNET_ACCOUNT",
                "authoritative": True,
                "balances": [
                    {"asset": "BTC", "free": "1.00000000", "locked": "0.00000000"},
                    {"asset": "ETH", "free": "10.00000000", "locked": "0.00000000"},
                    {"asset": "USDT", "free": "10000.00000000", "locked": "0.00000000"},
                ],
                "event_time_ms": observed_ms,
            }
        elif request.capability is Capability.OPEN_ORDERS_BY_SYMBOL:
            observation = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "OPEN_ORDERS_SNAPSHOT",
                "capability_id": "SPOT_TESTNET_OPEN_ORDERS_BY_SYMBOL",
                "authoritative": True,
                "requested_symbol": dict(request.parameters)["symbol"],
                "orders": [],
                "event_time_ms": observed_ms,
            }
        elif request.capability is Capability.SUBMIT_LIMIT_GTC:
            parameters = dict(request.parameters)
            observation = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "ORDER",
                "capability_id": "SPOT_TESTNET_SUBMIT_LIMIT_GTC",
                "authoritative": True,
                "found": True,
                "order": {
                    "client_order_id": parameters["newClientOrderId"],
                    "exchange_order_id": "12345",
                    "symbol": parameters["symbol"],
                    "side": parameters["side"],
                    "status": "NEW",
                    "quantity": parameters["quantity"],
                    "cumulative_filled_quantity": "0",
                    "limit_price": parameters["price"],
                    "event_time_ms": observed_ms,
                },
                "event_time_ms": observed_ms,
            }
        elif request.capability is Capability.CANCEL_BY_CLIENT_ID:
            parameters = dict(request.parameters)
            if self.order is None:
                raise ValueError("E2E_FAKE_CANCEL_ORDER_MISSING")
            observation = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "ORDER",
                "capability_id": "SPOT_TESTNET_CANCEL_BY_CLIENT_ID",
                "authoritative": True,
                "found": True,
                "order": {
                    "client_order_id": parameters["origClientOrderId"],
                    "exchange_order_id": str(self.order["exchange_order_id"]),
                    "symbol": str(self.order["symbol"]),
                    "side": str(self.order["side"]),
                    "status": "CANCELED",
                    "quantity": str(self.order["quantity"]),
                    "cumulative_filled_quantity": str(self.order["filled_quantity"]),
                    "limit_price": str(self.order["limit_price"]),
                    "event_time_ms": observed_ms,
                },
                "event_time_ms": observed_ms,
            }
        else:
            raise ValueError("E2E_FAKE_TRANSPORT_CAPABILITY_NOT_PINNED")
        return SanitizedTransportResult("EXCHANGE_ACKNOWLEDGED", 200, None, "0" * 64, observation)


class _PinnedFakeUserDataTransport:
    def __init__(self, order: dict[str, object], *, terminate: bool) -> None:
        self.order = order
        self.terminate = terminate
        self.fake_transport_calls = 0
        self.external_exchange_network_calls = 0

    async def events(self, *, request_id: str, timestamp_ms: str):
        del request_id, timestamp_ms
        self.fake_transport_calls += 1
        observed_ms = int(datetime.now(UTC).timestamp() * 1000)
        yield {
            "subscriptionId": 1,
            "event": {
                "e": "executionReport",
                "E": observed_ms,
                "s": self.order["symbol"],
                "c": self.order["client_order_id"],
                "S": self.order["side"],
                "X": "PARTIALLY_FILLED",
                "q": str(self.order["quantity"]),
                "p": str(self.order["limit_price"]),
                "z": "0.000100000000000000",
                "l": "0.000100000000000000",
                "L": str(self.order["limit_price"]),
                "n": "0.000000100000000000",
                "N": "BTC",
                "i": int(str(self.order["exchange_order_id"])),
                "t": 8001,
                "T": observed_ms,
            },
        }
        if self.terminate:
            yield {
                "subscriptionId": 1,
                "event": {"e": "eventStreamTerminated", "E": observed_ms + 1},
            }


def _testnet_fake_settings():
    from spot_testnet_gateway.settings import GatewaySettings, REST_ORIGIN, WS_URL

    binding = _testnet_runtime_binding()
    return GatewaySettings(
        True,
        "BINANCE_SPOT_TESTNET",
        REST_ORIGIN,
        WS_URL,
        Path("/e2e/fake-api-key"),
        Path("/e2e/fake-signing-secret"),
        binding.allowlist_digest,
        binding.gateway_instance_id,
        binding.build_digest,
        binding.configuration_digest,
    )


def _record_fake_transport_evidence(
    transport: _PinnedFakeTestnetTransport | _PinnedFakeUserDataTransport, mode: str
) -> None:
    target = Path(_required_environment("P8_GATEWAY_E2E_EVIDENCE_FILE"))
    target.parent.mkdir(parents=True, exist_ok=True)
    prior_calls = 0
    if target.exists():
        prior = json.loads(target.read_text(encoding="utf-8"))
        prior_calls = int(prior.get("fake_transport_calls", 0))
    evidence = {
        "schema_version": "woozoo.phase8-gateway-process-e2e/v1",
        "status": "PASS",
        "transport": "pinned-fake-spot-testnet-v1",
        "last_mode": mode,
        "production_worker_orchestration": True,
        "fake_transport_calls": prior_calls + transport.fake_transport_calls,
        "external_exchange_network_calls": transport.external_exchange_network_calls,
        "caller_supplied_endpoint_allowed": False,
    }
    target.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_testnet_gateway_preflight_fake() -> int:
    """Run production reconciliation orchestration with a pinned fake transport."""
    from spot_testnet_gateway.worker import run_gateway_reconciliation_cycle

    transport = _PinnedFakeTestnetTransport()
    result = run_gateway_reconciliation_cycle(
        _testnet_fake_settings(),
        _required_environment("SPOT_TESTNET_GATEWAY_DATABASE_URL"),
        transport=transport,
    )
    _record_fake_transport_evidence(transport, "preflight")
    print("P8_GATEWAY_PREFLIGHT:" + json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


def _latest_testnet_order(*, require_exchange_id: bool) -> dict[str, object] | None:
    predicate = "WHERE exchange_order_id IS NOT NULL " if require_exchange_id else ""
    with psycopg.connect(
        _required_environment("TESTNET_EXECUTION_DATABASE_URL"), row_factory=dict_row
    ) as connection:
        row = connection.execute(
            "SELECT client_order_id,symbol,side,quantity,limit_price,filled_quantity,"
            "exchange_order_id FROM testnet_orders "
            + predicate
            + "ORDER BY version DESC,order_id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row is not None else None


def run_testnet_gateway_dispatch_fake() -> int:
    """Run production command orchestration with a pinned fake transport."""
    from spot_testnet_gateway.worker import run_gateway_command_cycle

    transport = _PinnedFakeTestnetTransport(_latest_testnet_order(require_exchange_id=False))
    result = run_gateway_command_cycle(
        _testnet_fake_settings(),
        _required_environment("SPOT_TESTNET_GATEWAY_DATABASE_URL"),
        transport=transport,
    )
    if result is None:
        return 2
    _record_fake_transport_evidence(transport, "dispatch")
    print("P8_GATEWAY_RESULT:" + json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


def run_testnet_gateway_user_data_fake(*, terminate: bool) -> int:
    """Run the production User Data pump for a fill or an intentional gap."""
    import asyncio

    from spot_testnet_gateway.worker import run_gateway_user_data_session

    order = _latest_testnet_order(require_exchange_id=True)
    if order is None:
        return 2
    transport = _PinnedFakeUserDataTransport(order, terminate=terminate)
    result = asyncio.run(
        run_gateway_user_data_session(
            _testnet_fake_settings(),
            _required_environment("SPOT_TESTNET_GATEWAY_DATABASE_URL"),
            transport=transport,
        )
    )
    _record_fake_transport_evidence(transport, "user-data-fill" if terminate else "user-data-gap")
    print("P8_GATEWAY_USER_DATA:" + json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def bootstrap_public_data() -> int:
    """Drive recorded public data through the real Market/Evidence authorities."""

    from evidence_worker.runner import materialize_evidence_command
    from market_data_worker.persistence import PostgresMarketStore
    from market_data_worker.pipeline import CollectorPipeline
    from market_data_worker.types import QualityEvent, QualityStatus

    market_url = _required_environment("MARKET_DATABASE_URL")
    evidence_url = _required_environment("EVIDENCE_DATABASE_URL")
    paper_url = _required_environment("PAPER_DATABASE_URL")
    observed_at = datetime.now(UTC) - timedelta(seconds=2)
    session_id = str(uuid4())
    market_store = PostgresMarketStore(market_url)
    market_store.create_session(session_id, f"e2e-{session_id}", observed_at - timedelta(hours=90))
    pipeline = CollectorPipeline(market_store)
    pipeline.confirm_generation(session_id)

    receipt_offset = 0

    def ingest(stream: str, payload: dict[str, object]) -> None:
        nonlocal receipt_offset
        receipt_offset += 1
        received_at = (
            observed_at - timedelta(milliseconds=500) + timedelta(milliseconds=receipt_offset)
        )
        result = pipeline.ingest(session_id, stream, payload, received_at)
        if not result.accepted:
            raise RuntimeError(f"E2E_PUBLIC_DATA_REJECTED:{stream}:{result.reason}")

    prices = {"BTCUSDT": "60000.00", "ETHUSDT": "3000.00"}
    intervals = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }
    trade_sequence = {"BTCUSDT": 1000, "ETHUSDT": 2000}
    book_sequence = {"BTCUSDT": 3000, "ETHUSDT": 4000}

    # Prime every allowlisted stream with a historical point so the stale-data
    # browser scenario is backed by real immutable Evidence, not a route mock.
    for symbol in ("BTCUSDT", "ETHUSDT"):
        lower = symbol.lower()
        historical_at = observed_at - timedelta(minutes=11)
        event_ms = _milliseconds(historical_at)
        ingest(
            f"{lower}@trade",
            {
                "e": "trade",
                "E": event_ms,
                "s": symbol,
                "t": trade_sequence[symbol],
                "p": prices[symbol],
                "q": "0.01000000",
                "T": event_ms,
                "m": False,
                "M": True,
            },
        )
        ingest(
            f"{lower}@bookTicker",
            {
                "u": book_sequence[symbol],
                "s": symbol,
                "b": prices[symbol],
                "B": "10.00000000",
                "a": str(float(prices[symbol]) + 0.01),
                "A": "10.00000000",
            },
        )
        for interval, delta in intervals.items():
            close = observed_at - delta * 32
            open_time = close - delta
            close_ms = _milliseconds(close)
            ingest(
                f"{lower}@kline_{interval}",
                {
                    "e": "kline",
                    "E": close_ms,
                    "s": symbol,
                    "k": {
                        "t": _milliseconds(open_time),
                        "T": close_ms,
                        "s": symbol,
                        "i": interval,
                        "f": 1,
                        "L": 1,
                        "o": prices[symbol],
                        "c": prices[symbol],
                        "h": str(float(prices[symbol]) + 1),
                        "l": str(float(prices[symbol]) - 1),
                        "v": "10.00000000",
                        "n": 1,
                        "x": True,
                        "q": "100.00000000",
                        "V": "5.00000000",
                        "Q": "50.00000000",
                        "B": "0",
                    },
                },
            )

    # All later rows bind HEALTHY stream state. Each viewport gets an independent
    # proposal/authorization, so both symbols need a complete Evidence window.
    price_bases = {"BTCUSDT": 60000, "ETHUSDT": 3000}
    for symbol, price_base in price_bases.items():
        for interval, delta in intervals.items():
            for index in range(32):
                close = observed_at - delta * (31 - index)
                open_time = close - delta
                close_ms = _milliseconds(close)
                price = price_base + index
                ingest(
                    f"{symbol.lower()}@kline_{interval}",
                    {
                        "e": "kline",
                        "E": close_ms,
                        "s": symbol,
                        "k": {
                            "t": _milliseconds(open_time),
                            "T": close_ms,
                            "s": symbol,
                            "i": interval,
                            "f": index + 2,
                            "L": index + 2,
                            "o": f"{price}.00",
                            "c": f"{price}.00",
                            "h": f"{price + 1}.00",
                            "l": f"{price - 1}.00",
                            "v": "10.00000000",
                            "n": 1,
                            "x": True,
                            "q": "100.00000000",
                            "V": "5.00000000",
                            "Q": "50.00000000",
                            "B": "0",
                        },
                    },
                )

    # Refresh healthy books after all streams are complete; Risk binds both books.
    for symbol in ("BTCUSDT", "ETHUSDT"):
        lower = symbol.lower()
        trade_sequence[symbol] += 1
        current_ms = _milliseconds(observed_at - timedelta(seconds=1))
        ingest(
            f"{lower}@trade",
            {
                "e": "trade",
                "E": current_ms,
                "s": symbol,
                "t": trade_sequence[symbol],
                "p": prices[symbol],
                "q": "0.01000000",
                "T": current_ms,
                "m": False,
                "M": True,
            },
        )
        book_sequence[symbol] += 1
        ingest(
            f"{lower}@bookTicker",
            {
                "u": book_sequence[symbol],
                "s": symbol,
                "b": prices[symbol],
                "B": "10.00000000",
                "a": str(float(prices[symbol]) + 0.01),
                "A": "10.00000000",
            },
        )

    # Record the supervisor-equivalent generation transition through the
    # durable quality-event authority after every allowlisted stream is primed.
    market_store.append_quality(
        QualityEvent(
            QualityStatus.HEALTHY,
            "e2e_bootstrap_complete",
            "market_data",
            observed_at,
            None,
        )
    )

    for symbol in ("BTCUSDT", "ETHUSDT"):
        evidence_as_of = observed_at if symbol == "BTCUSDT" else observed_at - timedelta(minutes=10)
        materialize_evidence_command(
            {"TRADING_MODE": "paper", "EVIDENCE_DATABASE_URL": evidence_url},
            idempotency_key=f"e2e-{symbol.lower()}-evidence-v1",
            service_principal="internal-evidence-scheduler",
            symbol=symbol,
            as_of=evidence_as_of,
            knowledge_cutoff=observed_at,
            created_at=observed_at,
        )
    reconciliation = PostgresPaperStore(paper_url).reconcile(
        PAPER_ACCOUNT_ID,
        checkpoint_id="e2e-opening-reconciliation-v1",
        created_at=observed_at,
    )
    if reconciliation.status != "HEALTHY":
        raise RuntimeError(
            "E2E_OPENING_RECONCILIATION_FAILED:" + ",".join(reconciliation.mismatch_codes)
        )
    return 0


def refresh_recorded_market(symbol: str | None = None) -> int:
    """Keep the local recorded Risk book projections current."""

    from market_data_worker.persistence import PostgresMarketStore
    from market_data_worker.pipeline import CollectorPipeline
    from market_data_worker.recovery import PostgresRestartRepository

    market_url = _required_environment("MARKET_DATABASE_URL")
    market_store = PostgresMarketStore(market_url)
    snapshot = PostgresRestartRepository(market_url).load()
    pipeline = CollectorPipeline(market_store)
    pipeline.bootstrap_continuity(
        snapshot.session_id,
        snapshot.watermarks,
        snapshot.last_sequences,
        snapshot.stream_statuses,
        snapshot.closed_kline_opens,
    )
    for raw in snapshot.pending_raw:
        recovered = pipeline.recover_raw(raw)
        if not recovered.accepted and recovered.reason != "duplicate":
            raise RuntimeError(f"E2E_RAW_RECOVERY_FAILED:{raw.stream}:{recovered.reason}")

    prices = {"BTCUSDT": "60000.00", "ETHUSDT": "3000.00"}

    def refresh_books() -> None:
        symbols = (symbol,) if symbol is not None else ("BTCUSDT", "ETHUSDT")
        for refresh_symbol in symbols:
            now = datetime.now(UTC)
            sequence = (pipeline.last_sequence("book_ticker", refresh_symbol) or 0) + 1
            price = prices[refresh_symbol]
            result = pipeline.ingest(
                snapshot.session_id,
                f"{refresh_symbol.lower()}@bookTicker",
                {
                    "u": sequence,
                    "s": refresh_symbol,
                    "b": price,
                    "B": "10.00000000",
                    "a": "60000.01" if refresh_symbol == "BTCUSDT" else "3000.01",
                    "A": "10.00000000",
                },
                now,
            )
            if not result.accepted:
                raise RuntimeError(f"E2E_BOOK_REFRESH_REJECTED:{refresh_symbol}:{result.reason}")

    # Keep this path book-only: each durable writer transaction is relatively
    # expensive through the local Windows Docker proxy and Risk intentionally
    # rejects either symbol once its recorded book is older than five seconds.
    refresh_books()
    return 0


def refresh_evidence(
    symbol: str,
    non_crossing_buy_symbol: str | None = None,
    non_crossing_sell_symbol: str | None = None,
    *,
    materialize_evidence: bool = True,
) -> int:
    """Refresh both authoritative books, then materialize immutable Evidence."""
    from evidence_worker.runner import materialize_evidence_command
    from market_data_worker.persistence import PostgresMarketStore
    from market_data_worker.pipeline import CollectorPipeline
    from market_data_worker.recovery import PostgresRestartRepository
    from market_data_worker.types import QualityEvent, QualityStatus

    market_url = _required_environment("MARKET_DATABASE_URL")
    paper_url = _required_environment("PAPER_DATABASE_URL")
    market_store = PostgresMarketStore(market_url)
    snapshot = PostgresRestartRepository(market_url).load()
    pipeline = CollectorPipeline(market_store)
    pipeline.bootstrap_continuity(
        snapshot.session_id,
        snapshot.watermarks,
        snapshot.last_sequences,
        snapshot.stream_statuses,
        snapshot.closed_kline_opens,
    )
    for raw in snapshot.pending_raw:
        recovered = pipeline.recover_raw(raw)
        if not recovered.accepted and recovered.reason != "duplicate":
            raise RuntimeError(f"E2E_RAW_RECOVERY_FAILED:{raw.stream}:{recovered.reason}")
    prices = {"BTCUSDT": "60000.00", "ETHUSDT": "3000.00"}

    def refresh_public_market(*, include_trade: bool) -> None:
        for offset, refresh_symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
            now = datetime.now(UTC) + timedelta(milliseconds=offset)
            event_ms = _milliseconds(now)
            lower = refresh_symbol.lower()
            public_price = prices[refresh_symbol]
            best_bid = public_price
            best_ask = "60000.01" if refresh_symbol == "BTCUSDT" else "3000.01"
            if refresh_symbol == non_crossing_buy_symbol:
                # Preserve the outstanding BUY used by the later Kill journey while
                # still recording a fresh, healthy, real public-book observation.
                public_price = "61000.00" if refresh_symbol == "BTCUSDT" else "3100.00"
                best_bid = public_price
                best_ask = "61000.01" if refresh_symbol == "BTCUSDT" else "3100.01"
            if refresh_symbol == non_crossing_sell_symbol:
                # Preserve an outstanding SELL by keeping the public bid below its
                # limit while still refreshing both authoritative market streams.
                public_price = "59000.00" if refresh_symbol == "BTCUSDT" else "2900.00"
                best_bid = public_price
                best_ask = "59000.01" if refresh_symbol == "BTCUSDT" else "2900.01"
            trade = None
            if include_trade:
                trade_sequence = (pipeline.last_sequence("trade", refresh_symbol) or 0) + 1
                trade = pipeline.ingest(
                    snapshot.session_id,
                    f"{lower}@trade",
                    {
                        "e": "trade",
                        "E": event_ms,
                        "s": refresh_symbol,
                        "t": trade_sequence,
                        "p": public_price,
                        "q": "0.01000000",
                        "T": event_ms,
                        "m": False,
                        "M": True,
                    },
                    now,
                )
            book_sequence = (pipeline.last_sequence("book_ticker", refresh_symbol) or 0) + 1
            book = pipeline.ingest(
                snapshot.session_id,
                f"{lower}@bookTicker",
                {
                    "u": book_sequence,
                    "s": refresh_symbol,
                    "b": best_bid,
                    "B": "10.00000000",
                    "a": best_ask,
                    "A": "10.00000000",
                },
                now,
            )
            if (trade is not None and not trade.accepted) or not book.accepted:
                reason = trade.reason if trade is not None and trade.reason else book.reason
                raise RuntimeError(f"E2E_BOOK_REFRESH_REJECTED:{refresh_symbol}:{reason}")

    refresh_public_market(include_trade=True)

    # Drain the same inbound-less Paper worker path before Risk snapshots the
    # account. A non-crossing book still creates an immutable NO_FILL effect;
    # allowing that effect to land after Risk would correctly cause approval
    # drift and make the browser journey timing-dependent.
    paper_store = PostgresPaperStore(paper_url)

    def reconcile_if_needed() -> None:
        with psycopg.connect(paper_url) as connection:
            current_digest = paper_store.semantic_digest(PAPER_ACCOUNT_ID, connection=connection)
            latest = connection.execute(
                "SELECT input_digest,status FROM paper_reconciliation_checkpoints "
                "WHERE account_id=%s ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
                (PAPER_ACCOUNT_ID,),
            ).fetchone()
        if latest == (current_digest, "HEALTHY"):
            return
        try:
            result = paper_store.reconcile(
                PAPER_ACCOUNT_ID,
                checkpoint_id=f"e2e-refresh-reconciliation-{uuid4()}",
                created_at=datetime.now(UTC),
            )
        except psycopg.errors.UniqueViolation:
            # The live background worker won the same digest race.
            with psycopg.connect(paper_url) as connection:
                current_digest = paper_store.semantic_digest(
                    PAPER_ACCOUNT_ID, connection=connection
                )
                latest = connection.execute(
                    "SELECT input_digest,status FROM paper_reconciliation_checkpoints "
                    "WHERE account_id=%s ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
                    (PAPER_ACCOUNT_ID,),
                ).fetchone()
            if latest != (current_digest, "HEALTHY"):
                raise RuntimeError("E2E_RECONCILIATION_RACE_UNRESOLVED")
        else:
            if result.status != "HEALTHY":
                raise RuntimeError(
                    "E2E_REFRESH_RECONCILIATION_FAILED:" + ",".join(result.mismatch_codes)
                )

    for _attempt in range(64):
        reconcile_if_needed()
        result = paper_store.apply_next_phase7_recorded_book()
        if result is None:
            reconcile_if_needed()
            break
    else:
        raise RuntimeError("E2E_RECORDED_BOOK_DRAIN_LIMIT")

    if not materialize_evidence:
        market_store.append_quality(
            QualityEvent(
                QualityStatus.HEALTHY,
                "e2e_refresh_complete",
                "market_data",
                datetime.now(UTC),
                None,
            )
        )
        refresh_public_market(include_trade=False)
        return 0

    # Keep the requested Evidence window point-in-time valid even when the full
    # browser suite runs beyond the shortest (1m) interval.
    intervals = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }
    kline_sequence = pipeline.last_sequence("kline", symbol) or 0
    for interval, delta in intervals.items():
        last_open = snapshot.closed_kline_opens[(symbol, interval)]
        next_open = last_open + delta
        while next_open + delta <= datetime.now(UTC):
            kline_sequence += 1
            close = next_open + delta
            received_at = datetime.now(UTC)
            refreshed = pipeline.ingest(
                snapshot.session_id,
                f"{symbol.lower()}@kline_{interval}",
                {
                    "e": "kline",
                    "E": _milliseconds(close),
                    "s": symbol,
                    "k": {
                        "t": _milliseconds(next_open),
                        "T": _milliseconds(close),
                        "s": symbol,
                        "i": interval,
                        "f": kline_sequence,
                        "L": kline_sequence,
                        "o": prices[symbol],
                        "c": prices[symbol],
                        "h": "60001.00" if symbol == "BTCUSDT" else "3001.00",
                        "l": "59999.00" if symbol == "BTCUSDT" else "2999.00",
                        "v": "10.00000000",
                        "n": 1,
                        "x": True,
                        "q": "100.00000000",
                        "V": "5.00000000",
                        "Q": "50.00000000",
                        "B": "0",
                    },
                },
                received_at,
            )
            if not refreshed.accepted:
                raise RuntimeError(
                    f"E2E_KLINE_REFRESH_REJECTED:{symbol}:{interval}:{refreshed.reason}"
                )
            next_open += delta

    # A local process restart can leave the durable replay collector marked
    # stale/invalid even though the immutable rows and continuity are intact.
    # Only restore HEALTHY after both books and all requested candle windows
    # have been refreshed through the real writer authority.
    market_store.append_quality(
        QualityEvent(
            QualityStatus.HEALTHY,
            "e2e_refresh_complete",
            "market_data",
            datetime.now(UTC),
            None,
        )
    )

    now = datetime.now(UTC)
    materialize_evidence_command(
        {
            "TRADING_MODE": "paper",
            "EVIDENCE_DATABASE_URL": _required_environment("EVIDENCE_DATABASE_URL"),
        },
        idempotency_key=f"e2e-refresh-{symbol.lower()}-{uuid4()}",
        service_principal="internal-evidence-scheduler",
        symbol=symbol,
        as_of=now,
        knowledge_cutoff=now,
        created_at=now,
    )
    # Evidence materialization can be slower than the five-second recorded-book
    # Risk window. End every refresh with a book-only pass so the UI never
    # advertises an analysis action against already-expired fixture books.
    refresh_public_market(include_trade=False)
    return 0


def record_partial_book(symbol: str) -> int:
    """Append a post-order public book for the real Paper worker to consume."""
    from market_data_worker.persistence import PostgresMarketStore
    from market_data_worker.pipeline import CollectorPipeline
    from market_data_worker.recovery import PostgresRestartRepository

    market_url = _required_environment("MARKET_DATABASE_URL")
    snapshot = PostgresRestartRepository(market_url).load()
    pipeline = CollectorPipeline(PostgresMarketStore(market_url))
    pipeline.bootstrap_continuity(
        snapshot.session_id,
        snapshot.watermarks,
        snapshot.last_sequences,
        snapshot.stream_statuses,
        snapshot.closed_kline_opens,
    )
    observed_at = datetime.now(UTC)
    sequence = (pipeline.last_sequence("book_ticker", symbol) or 0) + 1
    price = "60000.00" if symbol == "BTCUSDT" else "3000.00"
    result = pipeline.ingest(
        snapshot.session_id,
        f"{symbol.lower()}@bookTicker",
        {
            "u": sequence,
            "s": symbol,
            "b": price,
            "B": "0.02000000" if symbol == "ETHUSDT" else "0.00100000",
            "a": "60000.01" if symbol == "BTCUSDT" else "3000.01",
            "A": "0.02000000" if symbol == "ETHUSDT" else "0.00100000",
        },
        observed_at,
    )
    if not result.accepted:
        raise RuntimeError(f"E2E_PARTIAL_BOOK_REJECTED:{symbol}:{result.reason}")
    return 0


def create_sell_analysis(
    symbol: str,
    non_crossing_buy_symbol: str | None = None,
    non_crossing_sell_symbol: str | None = None,
) -> int:
    """Persist a real E2E-only SELL analysis after E2E-001 acquires base asset."""
    import asyncio
    import json
    from collections.abc import Mapping

    from agent_orchestrator.models import Role
    from agent_orchestrator.persistence import PostgresAgentStore
    from agent_orchestrator.provider import MockLlmProvider
    from agent_orchestrator.workflow import AgentWorkflow
    from control_api.command_ports import PostgresRiskEvaluationPort

    class SellMockProvider(MockLlmProvider):
        async def invoke(self, role: Role, request: Mapping[str, object]) -> str:
            response = json.loads(await super().invoke(role, request))
            if role in {Role.TRADER, Role.PORTFOLIO}:
                response["stance"] = "SELL"
            return json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    now = datetime.now(UTC)
    store = PostgresAgentStore(_required_environment("AGENT_DATABASE_URL"))
    evidence_id = store.latest_healthy_evidence_id(symbol, now)
    if evidence_id is None:
        raise RuntimeError("E2E_SELL_EVIDENCE_UNAVAILABLE")
    evidence = store.load_evidence(evidence_id)
    if evidence is None:
        raise RuntimeError("E2E_SELL_EVIDENCE_MISSING")
    workflow = asyncio.run(
        AgentWorkflow(
            SellMockProvider(),
            clock=lambda: now.isoformat().replace("+00:00", "Z"),
            namespace="paper",
        ).run(evidence)
    )
    persisted = store.persist(workflow)
    if persisted.proposal_id is None or persisted.outcome != "COMPLETED":
        raise RuntimeError("E2E_SELL_ANALYSIS_HELD")
    # The mock workflow deliberately exercises several durable boundaries and
    # can outlive the five-second public-book window on a cold CI runner. Keep
    # the immutable Evidence/Proposal, but refresh and fully reconcile the same
    # protected public prices immediately before Risk snapshots authority.
    refresh_evidence(
        symbol,
        non_crossing_buy_symbol,
        non_crossing_sell_symbol,
        materialize_evidence=False,
    )
    risk_recorded_at = datetime.now(UTC)
    PostgresRiskEvaluationPort(_required_environment("RISK_DATABASE_URL")).evaluate_proposal(
        persisted.proposal_id,
        PAPER_ACCOUNT_ID,
        risk_recorded_at,
    )
    print(f"E2E_SELL_RUN_ID:{persisted.run_id}")
    print(f"E2E_SELL_PROPOSAL_ID:{persisted.proposal_id}")
    return 0


def create_live_app() -> FastAPI:
    if _required_environment("TRADING_MODE") != "paper":
        raise RuntimeError("Phase 7 E2E requires TRADING_MODE=paper")
    for name in (
        "DATABASE_URL",
        "CONTROL_DATABASE_URL",
        "AGENT_DATABASE_URL",
        "RISK_DATABASE_URL",
        "PAPER_DATABASE_URL",
        "REDIS_URL",
        "LOCAL_OPERATOR_ORIGIN",
        "LOCAL_OPERATOR_VERIFIER_FILE",
    ):
        _required_environment(name)
    # No dependency overrides: create_app constructs PostgresTradingRoom,
    # Postgres security, real probes, and dedicated Risk/Paper adapters.
    return create_app(environment=os.environ)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-once", action="store_true")
    parser.add_argument("--testnet-execution-once", action="store_true")
    parser.add_argument("--testnet-gateway-preflight-fake", action="store_true")
    parser.add_argument("--testnet-gateway-dispatch-fake", action="store_true")
    parser.add_argument("--testnet-gateway-user-data-fill-fake", action="store_true")
    parser.add_argument("--testnet-gateway-user-data-gap-fake", action="store_true")
    parser.add_argument("--reconcile-once", action="store_true")
    parser.add_argument("--bootstrap-public-data", action="store_true")
    parser.add_argument("--refresh-market", action="store_true")
    parser.add_argument("--market-symbol", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--refresh-evidence", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--non-crossing-buy-book", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--non-crossing-sell-book", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--record-partial-book", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--create-sell-analysis", choices=("BTCUSDT", "ETHUSDT"))
    args = parser.parse_args()
    if args.worker_once:
        return run_worker_once()
    if args.testnet_execution_once:
        return run_testnet_execution_once()
    if args.testnet_gateway_preflight_fake:
        return run_testnet_gateway_preflight_fake()
    if args.testnet_gateway_dispatch_fake:
        return run_testnet_gateway_dispatch_fake()
    if args.testnet_gateway_user_data_fill_fake:
        return run_testnet_gateway_user_data_fake(terminate=True)
    if args.testnet_gateway_user_data_gap_fake:
        return run_testnet_gateway_user_data_fake(terminate=False)
    if args.reconcile_once:
        return run_reconciliation_once()
    if args.bootstrap_public_data:
        return bootstrap_public_data()
    if args.refresh_market:
        return refresh_recorded_market(args.market_symbol)
    if args.refresh_evidence:
        return refresh_evidence(
            args.refresh_evidence,
            args.non_crossing_buy_book,
            args.non_crossing_sell_book,
        )
    if args.record_partial_book:
        return record_partial_book(args.record_partial_book)
    if args.create_sell_analysis:
        return create_sell_analysis(
            args.create_sell_analysis,
            args.non_crossing_buy_book,
            args.non_crossing_sell_book,
        )
    uvicorn.run(
        create_live_app(),
        host="127.0.0.1",
        port=8001,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
