"""Closed REST capability mapping; callers cannot supply a host or arbitrary path."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class Capability(StrEnum):
    TIME = "SPOT_TESTNET_TIME"
    EXCHANGE_INFO = "SPOT_TESTNET_EXCHANGE_INFO"
    SUBMIT_LIMIT_GTC = "SPOT_TESTNET_SUBMIT_LIMIT_GTC"
    CANCEL_BY_CLIENT_ID = "SPOT_TESTNET_CANCEL_BY_CLIENT_ID"
    QUERY_BY_CLIENT_ID = "SPOT_TESTNET_QUERY_BY_CLIENT_ID"
    OPEN_ORDERS_BY_SYMBOL = "SPOT_TESTNET_OPEN_ORDERS_BY_SYMBOL"
    ACCOUNT = "SPOT_TESTNET_ACCOUNT"
    MY_TRADES_BY_ORDER = "SPOT_TESTNET_MY_TRADES_BY_ORDER"


CAPABILITY_ROUTE: dict[Capability, tuple[str, str]] = {
    Capability.TIME: ("GET", "/api/v3/time"),
    Capability.EXCHANGE_INFO: ("GET", "/api/v3/exchangeInfo"),
    Capability.SUBMIT_LIMIT_GTC: ("POST", "/api/v3/order"),
    Capability.CANCEL_BY_CLIENT_ID: ("DELETE", "/api/v3/order"),
    Capability.QUERY_BY_CLIENT_ID: ("GET", "/api/v3/order"),
    Capability.OPEN_ORDERS_BY_SYMBOL: ("GET", "/api/v3/openOrders"),
    Capability.ACCOUNT: ("GET", "/api/v3/account"),
    Capability.MY_TRADES_BY_ORDER: ("GET", "/api/v3/myTrades"),
}
DECIMAL_PATTERN = re.compile(r"(?:0\.(?:0*[1-9][0-9]*)|[1-9][0-9]*(?:\.[0-9]+)?)")
CLIENT_ORDER_ID_PATTERN = re.compile(r"wz8-[a-f0-9]{32}")
TIMESTAMP_PATTERN = re.compile(r"[0-9]{13}")
ORDER_ID_PATTERN = re.compile(r"[0-9]+")
EXPECTED_FIELDS: dict[Capability, frozenset[str]] = {
    Capability.TIME: frozenset(),
    Capability.EXCHANGE_INFO: frozenset(),
    Capability.SUBMIT_LIMIT_GTC: frozenset(
        {
            "symbol",
            "side",
            "type",
            "timeInForce",
            "quantity",
            "price",
            "newClientOrderId",
            "recvWindow",
            "timestamp",
        }
    ),
    Capability.CANCEL_BY_CLIENT_ID: frozenset(
        {"symbol", "origClientOrderId", "recvWindow", "timestamp"}
    ),
    Capability.QUERY_BY_CLIENT_ID: frozenset(
        {"symbol", "origClientOrderId", "recvWindow", "timestamp"}
    ),
    Capability.OPEN_ORDERS_BY_SYMBOL: frozenset({"symbol", "recvWindow", "timestamp"}),
    Capability.ACCOUNT: frozenset({"recvWindow", "timestamp"}),
    Capability.MY_TRADES_BY_ORDER: frozenset({"symbol", "orderId", "recvWindow", "timestamp"}),
}


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    capability: Capability
    method: str
    path: str
    parameters: tuple[tuple[str, str], ...]


def validate_request(request: GatewayRequest) -> None:
    expected = CAPABILITY_ROUTE.get(request.capability)
    if expected is None or (request.method, request.path) != expected:
        raise ValueError("CAPABILITY_MISMATCH")
    if not request.path.startswith("/api/v3/") or "://" in request.path:
        raise ValueError("CAPABILITY_MISMATCH")
    if any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in request.parameters
    ):
        raise TypeError("CAPABILITY_PARAMETERS_MUST_BE_STRINGS")
    parameters = dict(request.parameters)
    if (
        len(parameters) != len(request.parameters)
        or frozenset(parameters) != EXPECTED_FIELDS[request.capability]
    ):
        raise ValueError("CAPABILITY_PARAMETERS_MISMATCH")
    if not parameters:
        return
    if parameters.get("symbol", "BTCUSDT") not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("CAPABILITY_PARAMETERS_MISMATCH")
    if (
        parameters.get("recvWindow") != "5000"
        or TIMESTAMP_PATTERN.fullmatch(parameters.get("timestamp", "")) is None
    ):
        raise ValueError("CAPABILITY_PARAMETERS_MISMATCH")
    if request.capability is Capability.SUBMIT_LIMIT_GTC:
        if (
            parameters["side"] not in {"BUY", "SELL"}
            or parameters["type"] != "LIMIT"
            or parameters["timeInForce"] != "GTC"
            or DECIMAL_PATTERN.fullmatch(parameters["quantity"]) is None
            or DECIMAL_PATTERN.fullmatch(parameters["price"]) is None
            or CLIENT_ORDER_ID_PATTERN.fullmatch(parameters["newClientOrderId"]) is None
        ):
            raise ValueError("CAPABILITY_PARAMETERS_MISMATCH")
    if request.capability in {Capability.CANCEL_BY_CLIENT_ID, Capability.QUERY_BY_CLIENT_ID}:
        if CLIENT_ORDER_ID_PATTERN.fullmatch(parameters["origClientOrderId"]) is None:
            raise ValueError("CAPABILITY_PARAMETERS_MISMATCH")
    if (
        request.capability is Capability.MY_TRADES_BY_ORDER
        and ORDER_ID_PATTERN.fullmatch(parameters["orderId"]) is None
    ):
        raise ValueError("CAPABILITY_PARAMETERS_MISMATCH")


def submit_limit_gtc_request(
    *,
    symbol: str,
    side: str,
    quantity: str,
    price: str,
    client_order_id: str,
    timestamp_ms: str,
) -> GatewayRequest:
    if symbol not in {"BTCUSDT", "ETHUSDT"} or side not in {"BUY", "SELL"}:
        raise ValueError("ORDER_ALLOWLIST_MISMATCH")
    request = GatewayRequest(
        Capability.SUBMIT_LIMIT_GTC,
        "POST",
        "/api/v3/order",
        (
            ("symbol", symbol),
            ("side", side),
            ("type", "LIMIT"),
            ("timeInForce", "GTC"),
            ("quantity", quantity),
            ("price", price),
            ("newClientOrderId", client_order_id),
            ("recvWindow", "5000"),
            ("timestamp", timestamp_ms),
        ),
    )
    validate_request(request)
    return request


def existing_order_request(
    *,
    capability: Capability,
    symbol: str,
    client_order_id: str,
    timestamp_ms: str,
) -> GatewayRequest:
    if capability not in {
        Capability.CANCEL_BY_CLIENT_ID,
        Capability.QUERY_BY_CLIENT_ID,
    }:
        raise ValueError("EXISTING_ORDER_CAPABILITY_MISMATCH")
    method, path = CAPABILITY_ROUTE[capability]
    request = GatewayRequest(
        capability,
        method,
        path,
        (
            ("symbol", symbol),
            ("origClientOrderId", client_order_id),
            ("recvWindow", "5000"),
            ("timestamp", timestamp_ms),
        ),
    )
    validate_request(request)
    return request


def reconciliation_request(
    *,
    capability: Capability,
    symbol: str | None,
    order_id: str | None,
    timestamp_ms: str,
) -> GatewayRequest:
    if capability not in {
        Capability.OPEN_ORDERS_BY_SYMBOL,
        Capability.ACCOUNT,
        Capability.MY_TRADES_BY_ORDER,
    }:
        raise ValueError("RECONCILIATION_CAPABILITY_MISMATCH")
    parameters: tuple[tuple[str, str], ...]
    if capability is Capability.ACCOUNT:
        parameters = (("recvWindow", "5000"), ("timestamp", timestamp_ms))
    elif capability is Capability.OPEN_ORDERS_BY_SYMBOL:
        if symbol is None:
            raise ValueError("RECONCILIATION_SYMBOL_REQUIRED")
        parameters = (
            ("symbol", symbol),
            ("recvWindow", "5000"),
            ("timestamp", timestamp_ms),
        )
    else:
        if symbol is None or order_id is None:
            raise ValueError("RECONCILIATION_ORDER_REQUIRED")
        parameters = (
            ("symbol", symbol),
            ("orderId", order_id),
            ("recvWindow", "5000"),
            ("timestamp", timestamp_ms),
        )
    method, path = CAPABILITY_ROUTE[capability]
    request = GatewayRequest(capability, method, path, parameters)
    validate_request(request)
    return request
