"""Loopback-only Phase 7 control API and Paper worker acceptance harness.

The HTTPS server owns disposable infrastructure setup.  This process uses the
same least-privilege database identities as the application and deliberately
does not expose a browser route for the Paper authorization worker.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

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


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def bootstrap_public_data() -> int:
    """Drive recorded public data through the real Market/Evidence authorities."""

    from evidence_worker.runner import materialize_evidence_command
    from market_data_worker.persistence import PostgresMarketStore
    from market_data_worker.pipeline import CollectorPipeline

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


def refresh_evidence(symbol: str) -> int:
    """Refresh both authoritative books, then materialize immutable Evidence."""
    from evidence_worker.runner import materialize_evidence_command
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
    prices = {"BTCUSDT": "60000.00", "ETHUSDT": "3000.00"}
    for offset, refresh_symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
        now = datetime.now(UTC) + timedelta(milliseconds=offset)
        event_ms = _milliseconds(now)
        trade_sequence = (pipeline.last_sequence("trade", refresh_symbol) or 0) + 1
        book_sequence = (pipeline.last_sequence("book_ticker", refresh_symbol) or 0) + 1
        lower = refresh_symbol.lower()
        trade = pipeline.ingest(
            snapshot.session_id,
            f"{lower}@trade",
            {
                "e": "trade",
                "E": event_ms,
                "s": refresh_symbol,
                "t": trade_sequence,
                "p": prices[refresh_symbol],
                "q": "0.01000000",
                "T": event_ms,
                "m": False,
                "M": True,
            },
            now,
        )
        book = pipeline.ingest(
            snapshot.session_id,
            f"{lower}@bookTicker",
            {
                "u": book_sequence,
                "s": refresh_symbol,
                "b": prices[refresh_symbol],
                "B": "10.00000000",
                "a": "60000.01" if refresh_symbol == "BTCUSDT" else "3000.01",
                "A": "10.00000000",
            },
            now,
        )
        if not trade.accepted or not book.accepted:
            raise RuntimeError(
                f"E2E_BOOK_REFRESH_REJECTED:{refresh_symbol}:{trade.reason or book.reason}"
            )

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
    parser.add_argument("--reconcile-once", action="store_true")
    parser.add_argument("--bootstrap-public-data", action="store_true")
    parser.add_argument("--refresh-evidence", choices=("BTCUSDT", "ETHUSDT"))
    args = parser.parse_args()
    if args.worker_once:
        return run_worker_once()
    if args.reconcile_once:
        return run_reconciliation_once()
    if args.bootstrap_public_data:
        return bootstrap_public_data()
    if args.refresh_evidence:
        return refresh_evidence(args.refresh_evidence)
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
