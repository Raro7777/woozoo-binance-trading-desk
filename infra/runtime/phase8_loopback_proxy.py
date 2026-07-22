from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from functools import partial


TARGETS: dict[str, tuple[str, int]] = {
    "postgres": ("postgres", 5432),
    "control-api": ("control-api", 8000),
}


def resolve_target(name: str) -> tuple[str, int]:
    try:
        return TARGETS[name]
    except KeyError as error:
        raise ValueError(f"unsupported loopback target: {name}") from error


async def _copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while payload := await reader.read(65_536):
            writer.write(payload)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()


async def _forward_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    target_host: str,
    target_port: int,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
    except OSError:
        client_writer.close()
        with suppress(ConnectionError):
            await client_writer.wait_closed()
        return

    tasks = {
        asyncio.create_task(_copy_stream(client_reader, upstream_writer)),
        asyncio.create_task(_copy_stream(upstream_reader, client_writer)),
    }
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def run(target_name: str) -> None:
    target_host, target_port = resolve_target(target_name)
    server = await asyncio.start_server(
        partial(
            _forward_connection,
            target_host=target_host,
            target_port=target_port,
        ),
        "0.0.0.0",
        target_port,
    )
    print(
        f"phase8 loopback proxy ready: {target_name} on {target_port}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=tuple(TARGETS))
    arguments = parser.parse_args()
    asyncio.run(run(arguments.target))


if __name__ == "__main__":
    main()
