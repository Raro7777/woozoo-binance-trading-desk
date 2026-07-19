from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import httpx
import pytest

from control_api.app import create_app


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql://postgres@127.0.0.1:5433/woozoo"
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
