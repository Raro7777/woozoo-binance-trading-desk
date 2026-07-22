"""Operator-activated observation-only preflight and User Data pumps."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
import time
from typing import Protocol

from .capabilities import Capability, GatewayRequest, reconciliation_request
from .observations import normalize_user_data_observation
from .persistence import PostgresGatewayObservationWriter
from .transport import SanitizedTransportResult


MAX_CLOCK_LEAD_MS = 1_000
MAX_CLOCK_LAG_MS = 5_000


class RestObservationTransport(Protocol):
    def send(self, request: GatewayRequest) -> SanitizedTransportResult: ...


class UserDataObservationTransport(Protocol):
    def events(self, *, request_id: str, timestamp_ms: str) -> AsyncIterator[dict[str, object]]: ...


class ReconciliationSupervisor:
    """Collect a complete initial account/open-order set without order capability."""

    def __init__(
        self,
        transport: RestObservationTransport,
        writer: PostgresGatewayObservationWriter,
    ) -> None:
        self._transport = transport
        self._writer = writer

    def _send(self, request: GatewayRequest) -> SanitizedTransportResult:
        # Recheck the database activation and running binary identity immediately
        # before every network call.  A revoked/expired activation yields zero call.
        self._writer.assert_authorized()
        result = self._transport.send(request)
        if result.status != "EXCHANGE_ACKNOWLEDGED" or result.observation is None:
            raise ValueError("RECONCILIATION_OBSERVATION_FAILED")
        self._writer.record(result.observation, source_channel="REST")
        return result

    def run_once(self) -> tuple[str, ...]:
        local_before_ms = int(time.time() * 1000)
        time_result = self._send(GatewayRequest(Capability.TIME, "GET", "/api/v3/time", ()))
        local_after_ms = int(time.time() * 1000)
        observation = time_result.observation
        server_time_value = observation.get("server_time_ms") if observation is not None else None
        if not isinstance(server_time_value, int) or isinstance(server_time_value, bool):
            raise ValueError("SERVER_TIME_OBSERVATION_INVALID")
        server_time_ms = server_time_value
        if (
            server_time_ms > local_after_ms + MAX_CLOCK_LEAD_MS
            or server_time_ms < local_before_ms - MAX_CLOCK_LAG_MS
        ):
            raise ValueError("SERVER_TIME_OFFSET_UNSAFE")
        timestamp_ms = str(server_time_ms)
        ids: list[str] = []
        for request in (
            reconciliation_request(
                capability=Capability.ACCOUNT,
                symbol=None,
                order_id=None,
                timestamp_ms=timestamp_ms,
            ),
            reconciliation_request(
                capability=Capability.OPEN_ORDERS_BY_SYMBOL,
                symbol="BTCUSDT",
                order_id=None,
                timestamp_ms=timestamp_ms,
            ),
            reconciliation_request(
                capability=Capability.OPEN_ORDERS_BY_SYMBOL,
                symbol="ETHUSDT",
                order_id=None,
                timestamp_ms=timestamp_ms,
            ),
        ):
            result = self._send(request)
            if result.observation is None:
                raise ValueError("RECONCILIATION_OBSERVATION_FAILED")
            # Content-addressed storage makes duplicate snapshots idempotent.  The
            # writer has already persisted it; return the digest for audit only.
            if result.payload_digest is not None:
                ids.append(result.payload_digest)
        return tuple(ids)


class UserDataObservationPump:
    """Persist normalized User Data events; unknown/gap events fail closed."""

    def __init__(
        self,
        transport: UserDataObservationTransport,
        writer: PostgresGatewayObservationWriter,
    ) -> None:
        self._transport = transport
        self._writer = writer

    async def events(self, *, request_id: str) -> AsyncIterator[str]:
        self._writer.assert_authorized()
        timestamp_ms = str(int(datetime.now(UTC).timestamp() * 1000))
        ended_normally = False
        try:
            async for envelope in self._transport.events(
                request_id=request_id,
                timestamp_ms=timestamp_ms,
            ):
                self._writer.assert_authorized()
                normalized = normalize_user_data_observation(envelope)
                if normalized["kind"] == "USER_DATA_TERMINATED":
                    ended_normally = True
                yield self._writer.record(normalized, source_channel="USER_DATA")
        except Exception:
            gap = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "USER_DATA_GAP",
                "capability_id": "SPOT_TESTNET_USER_DATA",
                "authoritative": True,
                "reason_code": "USER_DATA_TRANSPORT_FAILURE",
            }
            self._writer.record(gap, source_channel="USER_DATA")
            raise
        if not ended_normally:
            gap = {
                "schema_version": "woozoo.testnet-gateway-observation/v1",
                "kind": "USER_DATA_GAP",
                "capability_id": "SPOT_TESTNET_USER_DATA",
                "authoritative": True,
                "reason_code": "USER_DATA_STREAM_ENDED",
            }
            yield self._writer.record(gap, source_channel="USER_DATA")
