from __future__ import annotations

from pathlib import Path
import asyncio
import json
import time

import pytest

from spot_testnet_gateway.capabilities import (
    Capability,
    GatewayRequest,
    existing_order_request,
    reconciliation_request,
    validate_request,
)
from spot_testnet_gateway.settings import GatewaySettings
from spot_testnet_gateway.observations import (
    normalize_rest_observation,
    normalize_user_data_observation,
)
from spot_testnet_gateway.signing import canonical_query, hmac_sha256_signature
from spot_testnet_gateway.supervisor import ReconciliationSupervisor
from spot_testnet_gateway.transport import (
    SanitizedTransportResult,
    SignedRestTransport,
    UserDataWebSocketTransport,
    signed_user_data_subscription,
    validate_user_data_envelope,
)


HASH = "a" * 64


def write_gateway_secret(path: Path, value: str) -> None:
    path.write_text(value, "ascii")
    path.chmod(0o600)


def enabled_values(key_file: Path, secret_file: Path) -> dict[str, str]:
    return {
        "TRADING_MODE": "paper",
        "SPOT_TESTNET_GATEWAY_ENABLED": "true",
        "SPOT_TESTNET_ENVIRONMENT": "BINANCE_SPOT_TESTNET",
        "SPOT_TESTNET_REST_ORIGIN": "https://testnet.binance.vision",
        "SPOT_TESTNET_WS_URL": "wss://ws-api.testnet.binance.vision/ws-api/v3",
        "SPOT_TESTNET_API_KEY_FILE": str(key_file.resolve()),
        "SPOT_TESTNET_SIGNING_SECRET_FILE": str(secret_file.resolve()),
        "SPOT_TESTNET_ALLOWLIST_DIGEST": HASH,
        "SPOT_TESTNET_GATEWAY_INSTANCE_ID": "b" * 64,
        "SPOT_TESTNET_GATEWAY_BUILD_DIGEST": "c" * 64,
        "SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST": "d" * 64,
    }


def test_gateway_is_default_off_and_unknown_or_shared_secrets_fail_closed(tmp_path: Path) -> None:
    disabled = GatewaySettings.from_mapping({"TRADING_MODE": "paper"})
    assert disabled.deployment_enabled is False
    assert disabled.can_attempt_network is False

    with pytest.raises(ValueError, match="SPOT_TESTNET_GATEWAY_ENABLED"):
        GatewaySettings.from_mapping(
            {"TRADING_MODE": "paper", "SPOT_TESTNET_GATEWAY_ENABLED": "maybe"}
        )
    with pytest.raises(ValueError, match="TRADING_MODE"):
        GatewaySettings.from_mapping({"SPOT_TESTNET_GATEWAY_ENABLED": "false"})
    with pytest.raises(ValueError, match="RAW_SECRET_ENV_FORBIDDEN"):
        GatewaySettings.from_mapping(
            {
                "TRADING_MODE": "paper",
                "SPOT_TESTNET_GATEWAY_ENABLED": "false",
                "BINANCE_API_KEY": "do-not-store-secrets-here",
            }
        )

    key_file = tmp_path / "api-key"
    secret_file = tmp_path / "signing-secret"
    enabled = GatewaySettings.from_mapping(enabled_values(key_file, secret_file))
    assert enabled.deployment_enabled is True
    assert enabled.can_attempt_network is False
    assert enabled.api_key_file == key_file.resolve()


@pytest.mark.parametrize(
    ("capability", "method", "path"),
    [
        (Capability.TIME, "GET", "/api/v3/time"),
        (Capability.EXCHANGE_INFO, "GET", "/api/v3/exchangeInfo"),
        (Capability.SUBMIT_LIMIT_GTC, "POST", "/api/v3/order"),
        (Capability.CANCEL_BY_CLIENT_ID, "DELETE", "/api/v3/order"),
        (Capability.QUERY_BY_CLIENT_ID, "GET", "/api/v3/order"),
        (Capability.OPEN_ORDERS_BY_SYMBOL, "GET", "/api/v3/openOrders"),
        (Capability.ACCOUNT, "GET", "/api/v3/account"),
        (Capability.MY_TRADES_BY_ORDER, "GET", "/api/v3/myTrades"),
    ],
)
def test_exact_capability_allowlist(capability: Capability, method: str, path: str) -> None:
    common = (("recvWindow", "5000"), ("timestamp", "1655979030000"))
    parameters = {
        Capability.TIME: (),
        Capability.EXCHANGE_INFO: (),
        Capability.SUBMIT_LIMIT_GTC: (
            ("symbol", "BTCUSDT"),
            ("side", "BUY"),
            ("type", "LIMIT"),
            ("timeInForce", "GTC"),
            ("quantity", "0.001"),
            ("price", "60000.00"),
            ("newClientOrderId", "wz8-" + "a" * 32),
            *common,
        ),
        Capability.CANCEL_BY_CLIENT_ID: (
            ("symbol", "BTCUSDT"),
            ("origClientOrderId", "wz8-" + "a" * 32),
            *common,
        ),
        Capability.QUERY_BY_CLIENT_ID: (
            ("symbol", "BTCUSDT"),
            ("origClientOrderId", "wz8-" + "a" * 32),
            *common,
        ),
        Capability.OPEN_ORDERS_BY_SYMBOL: (("symbol", "BTCUSDT"), *common),
        Capability.ACCOUNT: common,
        Capability.MY_TRADES_BY_ORDER: (
            ("symbol", "BTCUSDT"),
            ("orderId", "123"),
            *common,
        ),
    }[capability]
    request = GatewayRequest(capability, method, path, parameters)
    validate_request(request)
    for mutated in (
        GatewayRequest(capability, "PUT", path, ()),
        GatewayRequest(capability, method, f"{path}/", ()),
        GatewayRequest(capability, method, "https://example.invalid/api/v3/order", ()),
    ):
        with pytest.raises(ValueError, match="CAPABILITY_MISMATCH"):
            validate_request(mutated)


def test_signed_query_is_deterministic_and_never_uses_float() -> None:
    parameters = (
        ("symbol", "BTCUSDT"),
        ("side", "BUY"),
        ("type", "LIMIT"),
        ("timeInForce", "GTC"),
        ("quantity", "0.00100000"),
        ("price", "60000.00"),
        ("newClientOrderId", "wz8-" + "a" * 32),
        ("recvWindow", "5000"),
        ("timestamp", "1655979030000"),
    )
    query = canonical_query(parameters)
    assert query.startswith("symbol=BTCUSDT&side=BUY&type=LIMIT")
    assert hmac_sha256_signature("NhqPtmdSJYdKjVH".encode(), query) == (
        "3386fd4794aa2c1636713c35716b74ae290bb80f7e5967abf625921472a580e6"
    )
    with pytest.raises(TypeError, match="string"):
        canonical_query((("quantity", 0.001),))  # type: ignore[arg-type]


def test_rest_transport_uses_exact_origin_no_retry_and_sanitizes_receipt(tmp_path: Path) -> None:
    key_file = tmp_path / "api-key"
    secret_file = tmp_path / "signing-secret"
    write_gateway_secret(key_file, "fixture-api-key")
    write_gateway_secret(secret_file, "fixture-signing-secret")
    settings = GatewaySettings.from_mapping(enabled_values(key_file, secret_file))
    calls: list[object] = []

    class Response:
        status = 200

        def read(self, amount: int = -1) -> bytes:
            del amount
            return (
                b'{"symbol":"BTCUSDT","orderId":123,"clientOrderId":"wz8-'
                + b"a" * 32
                + b'","side":"BUY","status":"NEW","origQty":"0.001",'
                b'"executedQty":"0","price":"60000","updateTime":1655979030000}'
            )

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

    def fake_open(request: object, timeout: float) -> Response:
        calls.append((request, timeout))
        return Response()

    transport = SignedRestTransport(settings, open_request=fake_open)
    result = transport.send(
        GatewayRequest(
            Capability.QUERY_BY_CLIENT_ID,
            "GET",
            "/api/v3/order",
            (
                ("symbol", "BTCUSDT"),
                ("origClientOrderId", "wz8-" + "a" * 32),
                ("recvWindow", "5000"),
                ("timestamp", "1655979030000"),
            ),
        )
    )
    assert result.status == "EXCHANGE_ACKNOWLEDGED"
    assert result.payload_digest is not None
    assert result.observation is not None
    assert result.observation["kind"] == "ORDER_QUERY"
    assert len(calls) == 1
    outbound = calls[0][0]
    assert getattr(outbound, "full_url").startswith("https://testnet.binance.vision/api/v3/order?")
    assert "fixture-api-key" not in repr(result)
    assert "fixture-signing-secret" not in repr(result)


def test_user_data_contract_is_exact_and_unknown_events_fail_closed() -> None:
    api_key = bytearray(b"fixture-api-key")
    signing_secret = bytearray(b"fixture-signing-secret")
    request = signed_user_data_subscription(
        request_id="subscription-1",
        api_key=api_key,
        signing_secret=signing_secret,
        timestamp_ms="1655979030000",
    )
    assert request["method"] == "userDataStream.subscribe.signature"
    assert (
        validate_user_data_envelope(
            '{"subscriptionId":1,"event":{"e":"executionReport","E":1655979030000}}'
        )["subscriptionId"]
        == 1
    )
    with pytest.raises(ValueError, match="NOT_ALLOWED"):
        validate_user_data_envelope('{"subscriptionId":1,"event":{"e":"listenKeyExpired"}}')


def test_rest_and_user_data_observations_are_closed_and_decimal_safe() -> None:
    order = normalize_rest_observation(
        Capability.QUERY_BY_CLIENT_ID,
        {
            "symbol": "BTCUSDT",
            "orderId": 123,
            "clientOrderId": "wz8-" + "a" * 32,
            "side": "BUY",
            "status": "PARTIALLY_FILLED",
            "origQty": "0.001",
            "executedQty": "0.0004",
            "price": "60000.00",
            "updateTime": 1655979030000,
            "ignoredRawField": "not persisted",
        },
    )
    assert order is not None
    assert order["kind"] == "ORDER_QUERY"
    assert "ignoredRawField" not in json.dumps(order)

    event = normalize_user_data_observation(
        {
            "subscriptionId": 1,
            "event": {
                "e": "executionReport",
                "E": 1655979030000,
                "T": 1655979030000,
                "s": "BTCUSDT",
                "c": "wz8-" + "a" * 32,
                "S": "BUY",
                "X": "PARTIALLY_FILLED",
                "q": "0.001",
                "p": "60000.00",
                "z": "0.0004",
                "i": 123,
                "t": 456,
                "l": "0.0004",
                "L": "60000.00",
                "n": "0.0000004",
                "N": "BTC",
            },
        }
    )
    assert event["kind"] == "USER_DATA_EXECUTION"
    assert event["fill"] is not None
    with pytest.raises(ValueError, match="ORDER_FILLED_QUANTITY_INVALID"):
        normalize_rest_observation(
            Capability.QUERY_BY_CLIENT_ID,
            {
                "symbol": "BTCUSDT",
                "orderId": 123,
                "clientOrderId": "wz8-" + "a" * 32,
                "side": "BUY",
                "status": "NEW",
                "origQty": "0.001",
                "executedQty": "NaN",
                "price": "60000.00",
                "updateTime": 1655979030000,
            },
        )


def test_existing_order_and_reconciliation_builders_are_closed() -> None:
    query = existing_order_request(
        capability=Capability.QUERY_BY_CLIENT_ID,
        symbol="BTCUSDT",
        client_order_id="wz8-" + "a" * 32,
        timestamp_ms="1655979030000",
    )
    assert query.method == "GET"
    account = reconciliation_request(
        capability=Capability.ACCOUNT,
        symbol=None,
        order_id=None,
        timestamp_ms="1655979030000",
    )
    assert account.path == "/api/v3/account"
    with pytest.raises(ValueError, match="CAPABILITY"):
        reconciliation_request(
            capability=Capability.SUBMIT_LIMIT_GTC,
            symbol="BTCUSDT",
            order_id=None,
            timestamp_ms="1655979030000",
        )


def test_reconciliation_supervisor_authorizes_before_every_observation_call() -> None:
    class Writer:
        def __init__(self) -> None:
            self.authority_checks = 0
            self.records: list[dict[str, object]] = []

        def assert_authorized(self) -> dict[str, object]:
            self.authority_checks += 1
            return {"activation_status": "ACTIVE"}

        def record(self, observation: dict[str, object], *, source_channel: str) -> str:
            assert source_channel == "REST"
            self.records.append(observation)
            return "a" * 64

    class Transport:
        def send(self, request: GatewayRequest) -> SanitizedTransportResult:
            if request.capability is Capability.TIME:
                observation = {
                    "schema_version": "woozoo.testnet-gateway-observation/v1",
                    "kind": "SERVER_TIME",
                    "capability_id": request.capability.value,
                    "authoritative": True,
                    "server_time_ms": int(time.time() * 1000),
                }
            elif request.capability is Capability.ACCOUNT:
                observation = {
                    "schema_version": "woozoo.testnet-gateway-observation/v1",
                    "kind": "ACCOUNT_SNAPSHOT",
                    "capability_id": request.capability.value,
                    "authoritative": True,
                    "event_time_ms": int(time.time() * 1000),
                    "balances": [],
                }
            else:
                observation = {
                    "schema_version": "woozoo.testnet-gateway-observation/v1",
                    "kind": "OPEN_ORDERS_SNAPSHOT",
                    "capability_id": request.capability.value,
                    "authoritative": True,
                    "requested_symbol": dict(request.parameters)["symbol"],
                    "orders": [],
                }
            return SanitizedTransportResult(
                "EXCHANGE_ACKNOWLEDGED", 200, None, "b" * 64, observation
            )

    writer = Writer()
    digests = ReconciliationSupervisor(Transport(), writer).run_once()  # type: ignore[arg-type]
    assert len(digests) == 3
    assert writer.authority_checks == 4
    assert [item["kind"] for item in writer.records] == [
        "SERVER_TIME",
        "ACCOUNT_SNAPSHOT",
        "OPEN_ORDERS_SNAPSHOT",
        "OPEN_ORDERS_SNAPSHOT",
    ]


def test_user_data_transport_connects_only_to_pinned_url_and_validates_events(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "api-key"
    secret_file = tmp_path / "signing-secret"
    write_gateway_secret(key_file, "fixture-api-key")
    write_gateway_secret(secret_file, "fixture-signing-secret")
    settings = GatewaySettings.from_mapping(enabled_values(key_file, secret_file))
    connected: list[str] = []
    sent: list[dict[str, object]] = []

    class FakeConnection:
        def __init__(self) -> None:
            self.messages = iter(
                (
                    '{"id":"subscription-1","status":200,"result":'
                    '{"subscriptionId":1},"rateLimits":[]}',
                    '{"subscriptionId":1,"event":{"e":"executionReport","E":1655979030000}}',
                )
            )

        async def __aenter__(self) -> FakeConnection:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def send(self, value: str) -> None:
            sent.append(json.loads(value))

        def __aiter__(self) -> FakeConnection:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self.messages)
            except StopIteration as error:
                raise StopAsyncIteration from error

    def connect(uri: str) -> FakeConnection:
        connected.append(uri)
        return FakeConnection()

    async def collect() -> list[dict[str, object]]:
        return [
            event
            async for event in UserDataWebSocketTransport(settings, connect=connect).events(
                request_id="subscription-1", timestamp_ms="1655979030000"
            )
        ]

    events = asyncio.run(collect())
    assert connected == ["wss://ws-api.testnet.binance.vision/ws-api/v3"]
    assert sent[0]["method"] == "userDataStream.subscribe.signature"
    assert events[0]["subscriptionId"] == 1
