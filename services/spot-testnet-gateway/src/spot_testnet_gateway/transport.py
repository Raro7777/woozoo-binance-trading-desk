"""Exact-origin REST and WebSocket transport for the isolated Gateway process.

The module is inert until an already authenticated Gateway command and activation
reach it. It never accepts a caller-provided host or path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from hashlib import sha256
import json
import ssl
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .capabilities import Capability, GatewayRequest, validate_request
from .observations import normalize_rest_observation
from .secrets import _zero, read_secret_file
from .settings import GatewaySettings, REST_ORIGIN, WS_URL
from .signing import canonical_query, hmac_sha256_signature


MAX_RESPONSE_BYTES = 1_048_576


class ResponseLike(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...
    def __enter__(self) -> ResponseLike: ...
    def __exit__(self, *args: object) -> None: ...


OpenRequest = Callable[[Request, float], ResponseLike]
SocketConnect = Callable[[str], Any]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


@dataclass(frozen=True, slots=True)
class SanitizedTransportResult:
    status: str
    http_status: int | None
    exchange_code: int | None
    payload_digest: str | None
    observation: dict[str, object] | None


def _default_open(request: Request, timeout: float) -> ResponseLike:
    # Explicitly disable inherited proxy configuration and redirects. TLS uses
    # the platform trust store and hostname validation.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)  # type: ignore[no-any-return]


def _bounded_payload(response: ResponseLike) -> bytes:
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("GATEWAY_RESPONSE_TOO_LARGE")
    return payload


def _exchange_code(payload: bytes) -> int | None:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    code = value.get("code") if isinstance(value, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


class SignedRestTransport:
    """No-retry REST sender with UNKNOWN classification for ambiguous outcomes."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        open_request: OpenRequest = _default_open,
        timeout_seconds: float = 10.0,
    ) -> None:
        if (
            not settings.deployment_enabled
            or settings.rest_origin != REST_ORIGIN
            or settings.api_key_file is None
            or settings.signing_secret_file is None
        ):
            raise ValueError("GATEWAY_TRANSPORT_DISABLED")
        if timeout_seconds <= 0 or timeout_seconds > 15:
            raise ValueError("GATEWAY_TIMEOUT_INVALID")
        self._settings = settings
        self._open = open_request
        self._timeout = timeout_seconds

    def send(self, request: GatewayRequest) -> SanitizedTransportResult:
        validate_request(request)
        query = canonical_query(request.parameters)
        headers = {
            "Accept": "application/json",
            "User-Agent": "woozoo-spot-testnet-gateway/1",
        }
        if request.capability in {Capability.TIME, Capability.EXCHANGE_INFO}:
            url = REST_ORIGIN + request.path
        else:
            api_key_file = self._settings.api_key_file
            signing_secret_file = self._settings.signing_secret_file
            if api_key_file is None or signing_secret_file is None:
                raise ValueError("GATEWAY_TRANSPORT_DISABLED")
            api_key = read_secret_file(api_key_file)
            signing_secret = read_secret_file(signing_secret_file)
            try:
                signature = hmac_sha256_signature(bytes(signing_secret), query)
                signed_query = f"{query}&signature={signature}"
                url = REST_ORIGIN + request.path + f"?{signed_query}"
                headers["X-MBX-APIKEY"] = bytes(api_key).decode("ascii")
            finally:
                _zero(api_key)
                _zero(signing_secret)
        outbound = Request(url, method=request.method, headers=headers)
        try:
            with self._open(outbound, self._timeout) as response:
                payload = _bounded_payload(response)
                status = response.status
        except HTTPError as error:
            payload = error.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("GATEWAY_RESPONSE_TOO_LARGE") from error
            status = error.code
        except (TimeoutError, ConnectionError, URLError, OSError):
            return SanitizedTransportResult("SUBMISSION_UNKNOWN", None, None, None, None)
        digest = sha256(payload).hexdigest()
        code = _exchange_code(payload)
        if status >= 500 or code == -1007:
            outcome = "SUBMISSION_UNKNOWN"
        elif 200 <= status < 300:
            outcome = "EXCHANGE_ACKNOWLEDGED"
        else:
            outcome = "RESOLVED_REJECTED"
        observation: dict[str, object] | None = None
        if 200 <= status < 300 or (
            request.capability is Capability.QUERY_BY_CLIENT_ID and code == -2013
        ):
            try:
                decoded = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("GATEWAY_RESPONSE_SCHEMA_INVALID") from error
            observation = normalize_rest_observation(
                request.capability,
                decoded,
                exchange_code=code,
                requested_symbol=dict(request.parameters).get("symbol"),
            )
        return SanitizedTransportResult(outcome, status, code, digest, observation)


def signed_user_data_subscription(
    *, request_id: str, api_key: bytearray, signing_secret: bytearray, timestamp_ms: str
) -> dict[str, object]:
    """Build the exact WS API signed subscription without logging credentials."""

    if not request_id or not timestamp_ms.isascii() or not timestamp_ms.isdigit():
        raise ValueError("USER_DATA_SUBSCRIPTION_INVALID")
    parameters = (
        ("apiKey", bytes(api_key).decode("ascii")),
        ("recvWindow", "5000"),
        ("timestamp", timestamp_ms),
    )
    signature = hmac_sha256_signature(bytes(signing_secret), canonical_query(parameters))
    return {
        "id": request_id,
        "method": "userDataStream.subscribe.signature",
        "params": {
            "apiKey": parameters[0][1],
            "recvWindow": 5000,
            "timestamp": int(timestamp_ms),
            "signature": signature,
        },
    }


def _default_user_data_connect(uri: str) -> Any:
    import websockets

    if uri != WS_URL:
        raise ValueError("GATEWAY_WS_ORIGIN_MISMATCH")
    return websockets.connect(
        uri,
        max_queue=1_000,
        max_size=MAX_RESPONSE_BYTES,
        ping_interval=20,
        ping_timeout=60,
        proxy=None,
    )


class UserDataWebSocketTransport:
    """Receive signed Spot Testnet User Data from the one pinned WS API URL."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        connect: SocketConnect = _default_user_data_connect,
    ) -> None:
        if (
            not settings.deployment_enabled
            or settings.websocket_url != WS_URL
            or settings.api_key_file is None
            or settings.signing_secret_file is None
        ):
            raise ValueError("GATEWAY_USER_DATA_DISABLED")
        self._settings = settings
        self._connect = connect

    async def events(
        self, *, request_id: str, timestamp_ms: str
    ) -> AsyncIterator[dict[str, object]]:
        api_key_file = self._settings.api_key_file
        signing_secret_file = self._settings.signing_secret_file
        if api_key_file is None or signing_secret_file is None:
            raise ValueError("GATEWAY_USER_DATA_DISABLED")
        api_key = read_secret_file(api_key_file)
        signing_secret = read_secret_file(signing_secret_file)
        try:
            subscription = signed_user_data_subscription(
                request_id=request_id,
                api_key=api_key,
                signing_secret=signing_secret,
                timestamp_ms=timestamp_ms,
            )
        finally:
            _zero(api_key)
            _zero(signing_secret)
        async with self._connect(WS_URL) as connection:
            await connection.send(json.dumps(subscription, sort_keys=True, separators=(",", ":")))
            subscribed = False
            async for message in connection:
                raw = message.decode("utf-8") if isinstance(message, bytes) else message
                if not isinstance(raw, str):
                    raise TypeError("USER_DATA_MESSAGE_TYPE_INVALID")
                if not subscribed:
                    subscribed = _validate_subscription_ack(raw, request_id=request_id)
                    continue
                yield validate_user_data_envelope(raw)


def _validate_subscription_ack(raw: str, *, request_id: str) -> bool:
    if len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("USER_DATA_EVENT_TOO_LARGE")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("USER_DATA_SUBSCRIPTION_REJECTED") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"id", "status", "result", "rateLimits"}
        or value.get("id") != request_id
        or value.get("status") != 200
        or not isinstance(value.get("result"), dict)
        or not isinstance(value.get("rateLimits"), list)
    ):
        raise ValueError("USER_DATA_SUBSCRIPTION_REJECTED")
    subscription_id = value["result"].get("subscriptionId")
    if not isinstance(subscription_id, int) or isinstance(subscription_id, bool):
        raise ValueError("USER_DATA_SUBSCRIPTION_REJECTED")
    return True


def validate_user_data_envelope(raw: str) -> dict[str, object]:
    """Accept only current WS API data envelopes; unknown shapes fail closed."""

    if len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("USER_DATA_EVENT_TOO_LARGE")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("USER_DATA_EVENT_INVALID") from error
    if not isinstance(value, dict) or set(value) != {"subscriptionId", "event"}:
        raise ValueError("USER_DATA_EVENT_INVALID")
    if not isinstance(value["subscriptionId"], int) or not isinstance(value["event"], dict):
        raise ValueError("USER_DATA_EVENT_INVALID")
    event_type = value["event"].get("e")
    if event_type not in {
        "executionReport",
        "outboundAccountPosition",
        "balanceUpdate",
        "eventStreamTerminated",
    }:
        raise ValueError("USER_DATA_EVENT_NOT_ALLOWED")
    return value


def pinned_transport_metadata() -> Iterator[str]:
    """Expose non-secret endpoint metadata for startup verification only."""

    yield REST_ORIGIN
    yield WS_URL
    yield ssl.OPENSSL_VERSION
