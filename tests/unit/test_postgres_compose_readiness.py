from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("compose_path", [ROOT / "compose.yaml", ROOT / "tests/e2e/compose.yaml"])
def test_postgres_healthcheck_uses_tcp_to_ignore_the_temporary_socket_server(
    compose_path: Path,
) -> None:
    compose = compose_path.read_text(encoding="utf-8")
    postgres_service = compose.split("\n  redis:", maxsplit=1)[0]
    assert (
        'test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres -d woozoo"]' in postgres_service
    )
