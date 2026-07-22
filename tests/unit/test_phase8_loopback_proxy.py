from __future__ import annotations

import asyncio
from functools import partial
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "infra" / "runtime" / "phase8_loopback_proxy.py"
SPEC = importlib.util.spec_from_file_location("phase8_loopback_proxy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PROXY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROXY
SPEC.loader.exec_module(PROXY)
_forward_connection = PROXY._forward_connection
resolve_target = PROXY.resolve_target


def test_loopback_proxy_only_resolves_fixed_internal_targets() -> None:
    assert resolve_target("postgres") == ("postgres", 5432)
    assert resolve_target("control-api") == ("control-api", 8000)

    with pytest.raises(ValueError, match="unsupported loopback target"):
        resolve_target("api.binance.com")


def test_loopback_proxy_forwards_bytes_without_interpreting_them() -> None:
    async def exercise() -> None:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while payload := await reader.read(4096):
                    writer.write(payload)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]
        proxy_server = await asyncio.start_server(
            partial(_forward_connection, target_host="127.0.0.1", target_port=echo_port),
            "127.0.0.1",
            0,
        )
        proxy_port = proxy_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            payload = b"woozoo-loopback-probe"
            writer.write(payload)
            await writer.drain()
            assert await reader.readexactly(len(payload)) == payload
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            echo_server.close()
            await proxy_server.wait_closed()
            await echo_server.wait_closed()

    asyncio.run(exercise())
