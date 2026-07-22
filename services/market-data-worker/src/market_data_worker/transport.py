"""Transport adapters whose callers can supply only typed public requests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
import json
from typing import Any, Protocol

import httpx

from .capabilities import PublicRestRequest, PublicStream, build_combined_stream_uri


class JsonResponse(Protocol):
    status_code: int
    headers: Any

    def json(self) -> object: ...


HttpGet = Callable[[str, float], JsonResponse]
SocketConnect = Callable[[str], Any]
MAX_REST_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PublicRestResponse:
    status_code: int
    headers: dict[str, str]
    payload: object | None
    raw_bytes: bytes


def default_get(uri: str, timeout: float) -> httpx.Response:
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        return client.get(uri, timeout=timeout)


class PublicRestTransport:
    def __init__(self, get: HttpGet | None = None, *, timeout_seconds: float = 10.0) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("public REST timeout must be in (0, 10] seconds")
        self._get = get or default_get
        self._timeout = timeout_seconds

    def fetch(self, request: PublicRestRequest) -> PublicRestResponse:
        if not isinstance(request, PublicRestRequest):
            raise TypeError("request must be a PublicRestRequest")
        response = self._get(request.uri, self._timeout)
        if 300 <= response.status_code < 400:
            raise RuntimeError("public REST redirects are not followed")
        content = getattr(response, "content", None)
        raw_bytes = (
            bytes(content)
            if content is not None
            else json.dumps(response.json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if len(raw_bytes) > MAX_REST_RESPONSE_BYTES:
            raise RuntimeError("public REST response exceeds the 8 MiB limit")
        try:
            payload: object | None = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        return PublicRestResponse(
            status_code=response.status_code,
            headers={str(name): str(value) for name, value in response.headers.items()},
            payload=payload,
            raw_bytes=raw_bytes,
        )


def default_connect(uri: str) -> Any:
    import websockets

    return websockets.connect(
        uri,
        max_queue=10_000,
        max_size=1_048_576,
        ping_interval=20,
        ping_timeout=60,
        proxy=None,
    )


class PublicWebSocketTransport:
    def __init__(self, connect: SocketConnect | None = None) -> None:
        self._connect = connect or default_connect

    async def messages(self, streams: tuple[PublicStream, ...]) -> AsyncIterator[bytes]:
        uri = build_combined_stream_uri(streams)
        async with self._connect(uri) as connection:
            async for message in connection:
                if isinstance(message, str):
                    yield message.encode("utf-8")
                elif isinstance(message, bytes):
                    yield message
                else:
                    raise TypeError("public stream message must be text or bytes")
