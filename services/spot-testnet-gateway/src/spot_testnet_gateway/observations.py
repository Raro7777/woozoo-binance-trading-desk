"""Closed, credential-free normalization of Spot Testnet observations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import cast

from .capabilities import Capability


ORDER_STATES = frozenset({"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED"})
SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})
SIDES = frozenset({"BUY", "SELL"})


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(code)
    return value


def _integer(value: object, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(code)
    return value


def _decimal(value: object, code: str, *, positive: bool = False) -> str:
    raw = _text(value, code)
    try:
        number = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(code) from error
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise ValueError(code)
    return format(number, "f")


def _signed_decimal(value: object, code: str) -> str:
    raw = _text(value, code)
    try:
        number = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(code) from error
    if not number.is_finite():
        raise ValueError(code)
    return format(number, "f")


def _order(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("ORDER_OBSERVATION_INVALID")
    symbol = _text(value.get("symbol"), "ORDER_SYMBOL_INVALID")
    side = _text(value.get("side"), "ORDER_SIDE_INVALID")
    status = _text(value.get("status"), "ORDER_STATUS_INVALID")
    if symbol not in SYMBOLS or side not in SIDES or status not in ORDER_STATES:
        raise ValueError("ORDER_OBSERVATION_INVALID")
    return {
        "client_order_id": _text(value.get("clientOrderId"), "CLIENT_ORDER_ID_INVALID"),
        "exchange_order_id": str(_integer(value.get("orderId"), "ORDER_ID_INVALID")),
        "symbol": symbol,
        "side": side,
        "status": status,
        "quantity": _decimal(value.get("origQty"), "ORDER_QUANTITY_INVALID", positive=True),
        "limit_price": _decimal(value.get("price"), "ORDER_PRICE_INVALID", positive=True),
        "cumulative_filled_quantity": _decimal(
            value.get("executedQty"), "ORDER_FILLED_QUANTITY_INVALID"
        ),
        "event_time_ms": _integer(
            value.get("updateTime", value.get("transactTime", 0)), "ORDER_TIME_INVALID"
        ),
    }


def _balance(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("BALANCE_OBSERVATION_INVALID")
    asset = _text(value.get("asset"), "BALANCE_ASSET_INVALID")
    if not asset.isascii() or not asset.isalnum() or asset.upper() != asset or len(asset) > 16:
        raise ValueError("BALANCE_ASSET_INVALID")
    return {
        "asset": asset,
        "free": _decimal(value.get("free"), "BALANCE_FREE_INVALID"),
        "locked": _decimal(value.get("locked"), "BALANCE_LOCKED_INVALID"),
    }


def _trade(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("TRADE_OBSERVATION_INVALID")
    return {
        "external_trade_id": str(_integer(value.get("id"), "TRADE_ID_INVALID")),
        "exchange_order_id": str(_integer(value.get("orderId"), "ORDER_ID_INVALID")),
        "symbol": _text(value.get("symbol"), "TRADE_SYMBOL_INVALID"),
        "quantity": _decimal(value.get("qty"), "TRADE_QUANTITY_INVALID", positive=True),
        "price": _decimal(value.get("price"), "TRADE_PRICE_INVALID", positive=True),
        "fee_amount": _decimal(value.get("commission"), "TRADE_FEE_INVALID"),
        "fee_asset": _text(value.get("commissionAsset"), "TRADE_FEE_ASSET_INVALID"),
        "event_time_ms": _integer(value.get("time"), "TRADE_TIME_INVALID"),
    }


def normalize_rest_observation(
    capability: Capability,
    value: object,
    *,
    exchange_code: int | None = None,
    requested_symbol: str | None = None,
) -> dict[str, object] | None:
    """Return a closed normalized value; raw exchange payload is never persisted."""

    if capability is Capability.QUERY_BY_CLIENT_ID and exchange_code == -2013:
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "ORDER_QUERY",
            "capability_id": capability.value,
            "authoritative": True,
            "found": False,
            "order": None,
        }
    if capability is Capability.TIME:
        if not isinstance(value, dict):
            raise ValueError("SERVER_TIME_OBSERVATION_INVALID")
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "SERVER_TIME",
            "capability_id": capability.value,
            "authoritative": True,
            "server_time_ms": _integer(value.get("serverTime"), "SERVER_TIME_INVALID"),
        }
    if capability in {
        Capability.SUBMIT_LIMIT_GTC,
        Capability.CANCEL_BY_CLIENT_ID,
        Capability.QUERY_BY_CLIENT_ID,
    }:
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "ORDER_QUERY" if capability is Capability.QUERY_BY_CLIENT_ID else "ORDER",
            "capability_id": capability.value,
            "authoritative": True,
            "found": True,
            "order": _order(value),
        }
    if capability is Capability.ACCOUNT:
        if not isinstance(value, dict) or not isinstance(value.get("balances"), list):
            raise ValueError("ACCOUNT_OBSERVATION_INVALID")
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "ACCOUNT_SNAPSHOT",
            "capability_id": capability.value,
            "authoritative": True,
            "event_time_ms": _integer(value.get("updateTime", 0), "ACCOUNT_TIME_INVALID"),
            "balances": [_balance(item) for item in cast(list[object], value["balances"])],
        }
    if capability is Capability.OPEN_ORDERS_BY_SYMBOL:
        if not isinstance(value, list) or requested_symbol not in SYMBOLS:
            raise ValueError("OPEN_ORDERS_OBSERVATION_INVALID")
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "OPEN_ORDERS_SNAPSHOT",
            "capability_id": capability.value,
            "authoritative": True,
            "requested_symbol": requested_symbol,
            "orders": [_order(item) for item in cast(list[object], value)],
        }
    if capability is Capability.MY_TRADES_BY_ORDER:
        if not isinstance(value, list):
            raise ValueError("TRADE_HISTORY_OBSERVATION_INVALID")
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "TRADE_HISTORY",
            "capability_id": capability.value,
            "authoritative": True,
            "fills": [_trade(item) for item in cast(list[object], value)],
        }
    return None


def normalize_user_data_observation(envelope: dict[str, object]) -> dict[str, object]:
    event = envelope.get("event")
    if not isinstance(event, dict):
        raise ValueError("USER_DATA_EVENT_INVALID")
    event_type = event.get("e")
    if event_type == "executionReport":
        order = {
            "client_order_id": _text(event.get("c"), "CLIENT_ORDER_ID_INVALID"),
            "exchange_order_id": str(_integer(event.get("i"), "ORDER_ID_INVALID")),
            "symbol": _text(event.get("s"), "ORDER_SYMBOL_INVALID"),
            "side": _text(event.get("S"), "ORDER_SIDE_INVALID"),
            "status": _text(event.get("X"), "ORDER_STATUS_INVALID"),
            "quantity": _decimal(event.get("q"), "ORDER_QUANTITY_INVALID", positive=True),
            "limit_price": _decimal(event.get("p"), "ORDER_PRICE_INVALID", positive=True),
            "cumulative_filled_quantity": _decimal(event.get("z"), "ORDER_FILLED_QUANTITY_INVALID"),
            "event_time_ms": _integer(event.get("E"), "ORDER_TIME_INVALID"),
        }
        if order["symbol"] not in SYMBOLS or order["side"] not in SIDES:
            raise ValueError("ORDER_OBSERVATION_INVALID")
        status = str(order["status"])
        if status not in ORDER_STATES:
            raise ValueError("ORDER_STATUS_INVALID")
        fill: dict[str, object] | None = None
        last_quantity = _decimal(event.get("l"), "TRADE_QUANTITY_INVALID")
        if Decimal(last_quantity) > 0:
            fill = {
                "external_trade_id": str(_integer(event.get("t"), "TRADE_ID_INVALID")),
                "exchange_order_id": order["exchange_order_id"],
                "symbol": order["symbol"],
                "quantity": last_quantity,
                "price": _decimal(event.get("L"), "TRADE_PRICE_INVALID", positive=True),
                "fee_amount": _decimal(event.get("n"), "TRADE_FEE_INVALID"),
                "fee_asset": _text(event.get("N"), "TRADE_FEE_ASSET_INVALID"),
                "event_time_ms": _integer(event.get("T", event.get("E")), "TRADE_TIME_INVALID"),
            }
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "USER_DATA_EXECUTION",
            "capability_id": "SPOT_TESTNET_USER_DATA",
            "authoritative": True,
            "subscription_id": _integer(envelope.get("subscriptionId"), "SUBSCRIPTION_ID_INVALID"),
            "order": order,
            "fill": fill,
        }
    if event_type == "outboundAccountPosition":
        raw_balances = event.get("B")
        if not isinstance(raw_balances, list):
            raise ValueError("ACCOUNT_OBSERVATION_INVALID")
        balances = [
            _balance({"asset": item.get("a"), "free": item.get("f"), "locked": item.get("l")})
            if isinstance(item, dict)
            else _balance(item)
            for item in raw_balances
        ]
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "ACCOUNT_DELTA",
            "capability_id": "SPOT_TESTNET_USER_DATA",
            "authoritative": True,
            "subscription_id": _integer(envelope.get("subscriptionId"), "SUBSCRIPTION_ID_INVALID"),
            "event_time_ms": _integer(event.get("E"), "ACCOUNT_TIME_INVALID"),
            "balances": balances,
        }
    if event_type == "balanceUpdate":
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "BALANCE_DELTA",
            "capability_id": "SPOT_TESTNET_USER_DATA",
            "authoritative": True,
            "subscription_id": _integer(envelope.get("subscriptionId"), "SUBSCRIPTION_ID_INVALID"),
            "event_time_ms": _integer(event.get("E"), "ACCOUNT_TIME_INVALID"),
            "asset": _text(event.get("a"), "BALANCE_ASSET_INVALID"),
            "delta": _signed_decimal(event.get("d"), "BALANCE_DELTA_INVALID"),
            "clear_time_ms": _integer(event.get("T"), "BALANCE_TIME_INVALID"),
        }
    if event_type == "eventStreamTerminated":
        return {
            "schema_version": "woozoo.testnet-gateway-observation/v1",
            "kind": "USER_DATA_TERMINATED",
            "capability_id": "SPOT_TESTNET_USER_DATA",
            "authoritative": True,
            "subscription_id": _integer(envelope.get("subscriptionId"), "SUBSCRIPTION_ID_INVALID"),
            "event_time_ms": _integer(event.get("E"), "USER_DATA_TIME_INVALID"),
        }
    raise ValueError("USER_DATA_EVENT_NOT_RECONCILABLE")
