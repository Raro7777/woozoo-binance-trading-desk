from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator
from uuid import uuid4
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb
import httpx
import pytest

from control_api.app import create_app
from market_data_worker.capabilities import KlineInterval, PublicRestRequest, RestCapability, Symbol
from market_data_worker.persistence import PostgresMarketStore
from market_data_worker.normalization import make_raw_event, normalize
import market_data_worker.persistence as market_persistence
from market_data_worker.pipeline import CollectorPipeline
from market_data_worker.recovery import PostgresRestartRepository, RestartCoordinator
from market_data_worker.replay import load_recorded_events
from market_data_worker.rest_collection import PublicRestCollector
from market_data_worker.transport import PublicRestTransport
from market_data_worker.types import (
    NormalizedMarketEvent,
    QualityEvent,
    QualityStatus,
    RawMarketEvent,
)


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
MARKET_WRITER_URL = "postgresql://woozoo_market_writer@127.0.0.1:5433/woozoo"
CONTROL_READER_URL = "postgresql://woozoo_control_reader@127.0.0.1:5433/woozoo"
REDIS_URL = "redis://127.0.0.1:6380/0"
ENVIRONMENT = {
    "TRADING_MODE": "paper",
    "DATABASE_URL": DATABASE_URL,
    "REDIS_URL": REDIS_URL,
}


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env={**os.environ, **ENVIRONMENT})


def get_health_response(app: object) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/v1/health")

    return asyncio.run(request())


@contextmanager
def integration_infrastructure_lock() -> Iterator[None]:
    path = ROOT / ".p1-integration.lock"
    with path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("could not acquire the Phase 1 infrastructure lock")
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@pytest.fixture(scope="module", autouse=True)
def platform_services() -> Iterator[None]:
    with integration_infrastructure_lock():
        run("docker", "compose", "up", "-d", "--wait", "postgres", "redis")
        try:
            run(sys.executable, "-m", "alembic", "upgrade", "head")
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute(
                    "TRUNCATE data_quality_events, normalized_market_events, "
                    "raw_market_events, market_status_projections, "
                    "stream_watermark_projections, collector_sessions, outbox_events "
                    "RESTART IDENTITY CASCADE"
                )
            yield
        finally:
            run("docker", "compose", "stop", "postgres", "redis")


def schema_digest() -> str:
    query = """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def test_plat_002_migration_is_reversible_and_repeatable() -> None:
    run(sys.executable, "-m", "alembic", "downgrade", "base")
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    first = schema_digest()
    run(sys.executable, "-m", "alembic", "downgrade", "base")
    run(sys.executable, "-m", "alembic", "upgrade", "head")

    assert schema_digest() == first


def test_plat_003_redis_loss_never_changes_postgres_authority() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    receipt_id = str(uuid4())
    request_hash = hashlib.sha256(receipt_id.encode("utf-8")).hexdigest()
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_receipts (receipt_id, request_hash, status, result, created_at)
                VALUES (%s, %s, %s, %s, now())
                """,
                (receipt_id, request_hash, "accepted", Jsonb({})),
            )
        connection.commit()

    run("docker", "compose", "stop", "redis")
    try:
        response = get_health_response(create_app(ENVIRONMENT))
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "degraded"

        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT request_hash, status FROM platform_receipts WHERE receipt_id = %s",
                    (receipt_id,),
                )
                assert cursor.fetchone() == (request_hash, "accepted")
    finally:
        run("docker", "compose", "up", "-d", "--wait", "redis")


def test_phase_two_market_history_is_durable_projected_and_append_only() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    trade_sequence = int(observed_at.timestamp() * 1_000_000)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"integration-{session_id}", observed_at)
    pipeline = CollectorPipeline(store)

    result = pipeline.ingest(
        session_id,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": int(observed_at.timestamp() * 1000),
            "s": "BTCUSDT",
            "t": trade_sequence,
            "p": "60000.10000000",
            "q": "0.01000000",
            "T": int(observed_at.timestamp() * 1000),
            "m": False,
            "M": True,
        },
        observed_at,
    )

    assert result.accepted and result.raw_event is not None
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT price, quality_status, quality_reasons "
                "FROM market_status_reader_v1 WHERE symbol='BTCUSDT'"
            )
            assert cursor.fetchone() == (
                "60000.100000000000000000",
                "degraded",
                ["stream_incomplete"],
            )
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cursor.execute(
                    "UPDATE raw_market_events SET payload_hash=%s WHERE id=%s",
                    ("0" * 64, result.raw_event.raw_event_id),
                )

    gap = pipeline.ingest(
        session_id,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": int(observed_at.timestamp() * 1000) + 1,
            "s": "BTCUSDT",
            "t": trade_sequence + 2,
            "p": "60001.10000000",
            "q": "0.01000000",
            "T": int(observed_at.timestamp() * 1000) + 1,
            "m": False,
            "M": True,
        },
        observed_at + timedelta(milliseconds=1),
    )
    assert not gap.accepted and gap.reason == "sequence_gap"
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT quality_status, quality_reasons FROM market_status_reader_v1 "
            "WHERE symbol='BTCUSDT'"
        ).fetchone()
        assert row == ("stale", ["sequence_gap"])
        event_types = dict(
            connection.execute(
                """
                SELECT event_type, count(*)
                FROM outbox_events
                WHERE payload->>'correlation_id'=%s
                GROUP BY event_type
                """,
                (session_id,),
            ).fetchall()
        )
        assert event_types == {
            "market.raw.appended.v1": 2,
            "market.normalized.recorded.v1": 1,
            "market.quality.changed.v1": 1,
        }
        outbox_payloads = connection.execute(
            "SELECT event_type, payload FROM outbox_events WHERE payload->>'correlation_id'=%s",
            (session_id,),
        ).fetchall()
        for event_type, payload in outbox_payloads:
            assert set(payload) == {
                "spec_version",
                "event_id",
                "event_type",
                "event_version",
                "occurred_at",
                "published_at",
                "producer",
                "correlation_id",
                "causation_id",
                "aggregate",
                "data",
                "payload_hash",
            }
            assert payload["event_type"] == event_type
            assert payload["producer"] == "market-data-worker"
            assert len(payload["payload_hash"]) == 64
            if event_type == "market.quality.changed.v1":
                assert payload["data"]["previous_status"] == "healthy"
                assert payload["data"]["new_status"] == "stale"

    with psycopg.connect(CONTROL_READER_URL) as connection:
        assert connection.execute(
            "SELECT quality_status FROM market_status_reader_v1 WHERE symbol='BTCUSDT'"
        ).fetchone() == ("stale",)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("UPDATE market_status_projections SET quality_status='healthy'")

    with psycopg.connect(MARKET_WRITER_URL) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "UPDATE raw_market_events SET payload_hash=%s WHERE id=%s",
                ("0" * 64, result.raw_event.raw_event_id),
            )


def test_market_raw_and_outbox_are_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"atomic-{session_id}", observed_at)
    raw = make_raw_event(
        session_id,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": int(observed_at.timestamp() * 1000),
            "s": "BTCUSDT",
            "t": 910_000_000,
            "p": "60000.1",
            "q": "0.01",
            "T": int(observed_at.timestamp() * 1000),
            "m": False,
            "M": True,
        },
        observed_at,
    )

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(market_persistence, "_append_outbox", fail_outbox)
    with pytest.raises(RuntimeError, match="outbox failure"):
        store.append_raw(raw)

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM raw_market_events WHERE id=%s", (raw.raw_event_id,)
        ).fetchone() == (0,)


def test_normalized_and_outbox_are_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"atomic-normalized-{session_id}", observed_at)
    raw = make_raw_event(
        session_id,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": int(observed_at.timestamp() * 1000),
            "s": "BTCUSDT",
            "t": int(observed_at.timestamp() * 1_000_000),
            "p": "60000.1",
            "q": "0.01",
            "T": int(observed_at.timestamp() * 1000),
            "m": False,
            "M": True,
        },
        observed_at,
    )
    assert store.append_raw(raw)
    normalized = normalize(raw)

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected normalized outbox failure")

    monkeypatch.setattr(market_persistence, "_append_outbox", fail_outbox)
    with pytest.raises(RuntimeError, match="normalized outbox failure"):
        store.append_normalized(normalized)

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM normalized_market_events WHERE id=%s", (normalized.event_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM stream_watermark_projections WHERE collector_session_id=%s",
            (session_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM market_status_projections WHERE last_event_id=%s",
            (normalized.event_id,),
        ).fetchone() == (0,)


def test_quality_and_outbox_are_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"atomic-quality-{session_id}", observed_at)
    raw = make_raw_event(
        session_id,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": int(observed_at.timestamp() * 1000),
            "s": "BTCUSDT",
            "t": int(observed_at.timestamp() * 1_000_000),
            "p": "60000.1",
            "q": "0.01",
            "T": int(observed_at.timestamp() * 1000),
            "m": False,
            "M": True,
        },
        observed_at,
    )
    assert store.append_raw(raw)
    store.append_normalized(normalize(raw))

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected quality outbox failure")

    monkeypatch.setattr(market_persistence, "_append_outbox", fail_outbox)
    with pytest.raises(RuntimeError, match="quality outbox failure"):
        store.append_quality(
            QualityEvent(
                QualityStatus.STALE,
                "injected_quality_failure",
                raw.stream,
                observed_at + timedelta(seconds=1),
                raw.raw_event_id,
            )
        )

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM data_quality_events WHERE raw_event_id=%s",
            (raw.raw_event_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT quality_status FROM stream_watermark_projections "
            "WHERE collector_session_id=%s AND stream=%s",
            (session_id, raw.stream),
        ).fetchone() == ("healthy",)
        assert connection.execute(
            "SELECT quality_status FROM market_status_projections WHERE symbol='BTCUSDT'"
        ).fetchone() == ("healthy",)


def test_global_disconnect_durably_downgrades_the_status_projection() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    trade_sequence = int(observed_at.timestamp() * 1_000_000)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"disconnect-{session_id}", observed_at)
    pipeline = CollectorPipeline(store)
    accepted = pipeline.ingest(
        session_id,
        "btcusdt@trade",
        {
            "e": "trade",
            "E": int(observed_at.timestamp() * 1000),
            "s": "BTCUSDT",
            "t": trade_sequence,
            "p": "60000.1",
            "q": "0.01",
            "T": int(observed_at.timestamp() * 1000),
            "m": False,
            "M": True,
        },
        observed_at,
    )
    assert accepted.accepted

    pipeline.mark_global_failure(
        QualityStatus.RECONNECTING,
        "socket_disconnected",
        observed_at + timedelta(seconds=1),
    )

    with psycopg.connect(CONTROL_READER_URL) as connection:
        assert connection.execute(
            "SELECT quality_status, quality_reasons FROM market_status_reader_v1 "
            "WHERE symbol='BTCUSDT'"
        ).fetchone() == ("reconnecting", ["socket_disconnected"])
    with psycopg.connect(DATABASE_URL) as connection:
        previous_statuses = connection.execute(
            "SELECT payload->'data'->>'previous_status' FROM outbox_events "
            "WHERE event_type='market.quality.changed.v1' "
            "AND payload->>'correlation_id'=%s "
            "AND payload->'data'->'reason_codes'->>0='socket_disconnected'",
            (session_id,),
        ).fetchall()
        assert sorted(status for (status,) in previous_statuses) == ["degraded"] * 11 + ["healthy"]


def test_future_kline_quarantine_downgrades_durable_symbol_projection() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    sequence = int(observed_at.timestamp() * 1_000_000)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"future-kline-{session_id}", observed_at)
    assert (
        CollectorPipeline(store)
        .ingest(
            session_id,
            "btcusdt@trade",
            {
                "e": "trade",
                "E": int(observed_at.timestamp() * 1000),
                "s": "BTCUSDT",
                "t": sequence,
                "p": "60000.1",
                "q": "0.01",
                "T": int(observed_at.timestamp() * 1000),
                "m": False,
                "M": True,
            },
            observed_at,
        )
        .accepted
    )

    class FutureKlineResponse:
        status_code = 200
        headers: dict[str, str] = {}
        content = json.dumps(
            [
                [
                    int(observed_at.timestamp() * 1000),
                    "60000",
                    "60001",
                    "59999",
                    "60000.1",
                    "10",
                    int((observed_at + timedelta(minutes=1)).timestamp() * 1000),
                    "600000",
                    1,
                    "5",
                    "300000",
                    "0",
                ]
            ],
            separators=(",", ":"),
        ).encode()

        def json(self) -> object:
            return json.loads(self.content)

    collector = PublicRestCollector(
        PublicRestTransport(get=lambda _uri, _timeout: FutureKlineResponse()), store
    )
    result = collector.collect(
        PublicRestRequest(
            RestCapability.KLINES,
            Symbol.BTCUSDT,
            interval=KlineInterval.ONE_MINUTE,
        ),
        session_id,
        observed_at + timedelta(seconds=1),
    )
    assert result.normalized_count == 0
    with psycopg.connect(CONTROL_READER_URL) as connection:
        assert connection.execute(
            "SELECT quality_status, quality_reasons FROM market_status_reader_v1 "
            "WHERE symbol='BTCUSDT'"
        ).fetchone() == ("invalid", ["kline_incomplete"])

    app = create_app(
        {
            "TRADING_MODE": "paper",
            "DATABASE_URL": CONTROL_READER_URL,
            "REDIS_URL": REDIS_URL,
        }
    )

    async def request_market_status() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/v1/markets/BTCUSDT/status")

    response = asyncio.run(request_market_status())
    assert response.status_code == 200
    assert response.json()["data"]["quality"] == "invalid"
    assert response.json()["data"]["quality_reasons"] == ["kline_incomplete"]


def test_postgres_restart_recovers_pending_raw_and_preserves_trade_continuity() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    with psycopg.connect(DATABASE_URL) as connection:
        next_sequence = int(
            connection.execute(
                "SELECT COALESCE(max(sequence),0)+1 FROM ("
                "SELECT sequence FROM raw_market_events "
                "WHERE stream='ethusdt@trade' AND symbol='ETHUSDT' "
                "UNION ALL "
                "SELECT sequence FROM normalized_market_events "
                "WHERE event_type='trade' AND symbol='ETHUSDT'"
                ") AS durable_trade_sequences"
            ).fetchone()[0]
        )

    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    durable = PostgresMarketStore(MARKET_WRITER_URL)
    durable.create_session(session_id, f"restart-{session_id}", observed_at)

    class CrashAfterRaw:
        def __init__(self) -> None:
            self.failed = False

        def append_raw(self, event: RawMarketEvent) -> bool:
            return durable.append_raw(event)

        def append_normalized(self, event: NormalizedMarketEvent) -> None:
            if not self.failed:
                self.failed = True
                raise OSError("injected crash after raw commit")
            durable.append_normalized(event)

        def append_quality(self, event: QualityEvent) -> None:
            durable.append_quality(event)

    payload = {
        "e": "trade",
        "E": int(observed_at.timestamp() * 1000),
        "s": "ETHUSDT",
        "t": next_sequence,
        "p": "3000.1",
        "q": "0.1",
        "T": int(observed_at.timestamp() * 1000),
        "m": False,
        "M": True,
    }
    failed = CollectorPipeline(CrashAfterRaw()).ingest(
        session_id, "ethusdt@trade", payload, observed_at
    )
    assert failed.reason == "normalized_append_failed"

    restarted = CollectorPipeline(durable)
    recovered = RestartCoordinator(PostgresRestartRepository(MARKET_WRITER_URL)).restore(restarted)
    assert recovered == 1
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute(
            "SELECT count(*) FROM normalized_market_events WHERE sequence=%s AND symbol='ETHUSDT'",
            (next_sequence,),
        ).fetchone() == (1,)

    next_session = str(uuid4())
    durable.create_session(next_session, f"restart-next-{next_session}", observed_at)
    restarted.confirm_generation(next_session)
    gap = restarted.ingest(
        next_session,
        "ethusdt@trade",
        {**payload, "t": next_sequence + 2},
        observed_at + timedelta(seconds=1),
    )
    assert not gap.accepted and gap.reason == "sequence_gap"


def test_postgres_restart_replay_preserves_semantic_digest_and_single_effect() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "TRUNCATE data_quality_events, normalized_market_events, "
            "raw_market_events, market_status_projections, "
            "stream_watermark_projections, collector_sessions, outbox_events "
            "RESTART IDENTITY CASCADE"
        )
    fixture = ROOT / "tests" / "fixtures" / "market-data" / "recorded-events.jsonl"
    events = load_recorded_events(fixture)
    session_id = events[0].session_id
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"recorded-{session_id}", events[0].received_at)
    pipeline = CollectorPipeline(store)
    for event in events:
        assert pipeline.ingest(
            event.session_id, event.stream, event.payload, event.received_at
        ).accepted

    def semantic_digest() -> tuple[str, tuple[int, int, int]]:
        with psycopg.connect(DATABASE_URL) as connection:
            normalized = connection.execute(
                "SELECT event_type, symbol, event_time::text, received_at::text, sequence, "
                "raw_payload_hash, quality_status, quality_reasons::text, "
                "stream_watermark::text, payload::text "
                "FROM normalized_market_events "
                "ORDER BY symbol, event_type, event_time, sequence, raw_payload_hash"
            ).fetchall()
            quality = connection.execute(
                "SELECT scope, sequence_no, status, reason, observed_at::text, raw_event_id "
                "FROM data_quality_events ORDER BY scope, sequence_no"
            ).fetchall()
            watermarks = connection.execute(
                "SELECT stream, last_sequence, observed_at::text, quality_status "
                "FROM stream_watermark_projections ORDER BY stream"
            ).fetchall()
            projection = connection.execute(
                "SELECT symbol, price::text, event_time::text, received_at::text, "
                "quality_status, quality_reasons::text, stream_watermark::text "
                "FROM market_status_projections ORDER BY symbol"
            ).fetchall()
            counts = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM raw_market_events), "
                "(SELECT count(*) FROM normalized_market_events), "
                "(SELECT count(*) FROM outbox_events)"
            ).fetchone()
        canonical = json.dumps(
            {
                "normalized": normalized,
                "quality_transitions": quality,
                "watermarks": watermarks,
                "market_projection": projection,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        assert counts is not None
        return hashlib.sha256(canonical.encode()).hexdigest(), tuple(int(item) for item in counts)

    before_digest, before_counts = semantic_digest()
    restarted = CollectorPipeline(PostgresMarketStore(MARKET_WRITER_URL))
    assert RestartCoordinator(PostgresRestartRepository(MARKET_WRITER_URL)).restore(restarted) == 0
    duplicate_results = [
        restarted.ingest(event.session_id, event.stream, event.payload, event.received_at)
        for event in events
    ]
    after_digest, after_counts = semantic_digest()

    assert all(not result.accepted and result.reason == "duplicate" for result in duplicate_results)
    assert before_digest == after_digest
    assert before_counts == after_counts == (5, 5, 10)


def test_book_ticker_final_stream_persists_healthy_symbol_completeness() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    milliseconds = int(observed_at.timestamp() * 1000)
    base_sequence = int(observed_at.timestamp() * 1_000_000)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"complete-{session_id}", observed_at)
    pipeline = CollectorPipeline(store)

    trade = {
        "e": "trade",
        "E": milliseconds,
        "s": "BTCUSDT",
        "t": base_sequence,
        "p": "60000.1",
        "q": "0.1",
        "T": milliseconds,
        "m": False,
        "M": True,
    }
    assert pipeline.ingest(session_id, "btcusdt@trade", trade, observed_at).accepted
    for offset, interval in enumerate(("1m", "5m", "1h", "4h"), start=1):
        kline = {
            "e": "kline",
            "E": milliseconds,
            "s": "BTCUSDT",
            "k": {
                "t": milliseconds - 60_000,
                "T": milliseconds - 1,
                "s": "BTCUSDT",
                "i": interval,
                "f": base_sequence + offset,
                "L": base_sequence + offset,
                "o": "60000",
                "c": "60000.1",
                "h": "60001",
                "l": "59999",
                "v": "10",
                "n": 1,
                "x": False,
                "q": "600000",
                "V": "5",
                "Q": "300000",
                "B": "0",
            },
        }
        assert pipeline.ingest(session_id, f"btcusdt@kline_{interval}", kline, observed_at).accepted
    book = {
        "u": base_sequence + 10,
        "s": "BTCUSDT",
        "b": "60000.0",
        "B": "1",
        "a": "60000.2",
        "A": "2",
    }
    final = pipeline.ingest(session_id, "btcusdt@bookTicker", book, observed_at)
    assert final.accepted and final.normalized is not None
    assert final.normalized.quality_status is QualityStatus.HEALTHY

    with psycopg.connect(CONTROL_READER_URL) as connection:
        assert connection.execute(
            "SELECT quality_status, quality_reasons FROM market_status_reader_v1 "
            "WHERE symbol='BTCUSDT'"
        ).fetchone() == ("healthy", [])


def test_postgres_restart_restores_closed_kline_grid_continuity() -> None:
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    session_id = str(uuid4())
    observed_at = datetime.now(tz=UTC)
    base_sequence = int(observed_at.timestamp() * 1_000_000)
    first_open = observed_at.replace(second=0, microsecond=0) - timedelta(minutes=3)
    store = PostgresMarketStore(MARKET_WRITER_URL)
    store.create_session(session_id, f"kline-restart-{session_id}", observed_at)

    def kline(open_time: datetime, sequence: int) -> dict[str, object]:
        opened = int(open_time.timestamp() * 1000)
        closed = opened + 59_999
        return {
            "e": "kline",
            "E": closed,
            "s": "ETHUSDT",
            "k": {
                "t": opened,
                "T": closed,
                "s": "ETHUSDT",
                "i": "1m",
                "f": sequence,
                "L": sequence,
                "o": "3000",
                "c": "3000.1",
                "h": "3001",
                "l": "2999",
                "v": "10",
                "n": 1,
                "x": True,
                "q": "30000",
                "V": "5",
                "Q": "15000",
                "B": "0",
            },
        }

    pipeline = CollectorPipeline(store)
    assert pipeline.ingest(
        session_id,
        "ethusdt@kline_1m",
        kline(first_open, base_sequence),
        observed_at,
    ).accepted

    restarted = CollectorPipeline(store)
    RestartCoordinator(PostgresRestartRepository(MARKET_WRITER_URL)).restore(restarted)
    next_session = str(uuid4())
    store.create_session(next_session, f"kline-next-{next_session}", observed_at)
    restarted.confirm_generation(next_session)
    assert restarted.ingest(
        next_session,
        "ethusdt@kline_1m",
        kline(first_open + timedelta(minutes=1), base_sequence + 1),
        observed_at,
    ).accepted
    skipped = restarted.ingest(
        next_session,
        "ethusdt@kline_1m",
        kline(first_open + timedelta(minutes=3), base_sequence + 3),
        observed_at + timedelta(seconds=1),
    )
    assert not skipped.accepted and skipped.reason == "kline_gap"
