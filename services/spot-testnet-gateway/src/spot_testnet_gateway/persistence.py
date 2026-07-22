"""Postgres write-ahead Gateway dispatch with crash-safe UNKNOWN recovery."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .canonical import CanonicalValue, canonical_digest
from .dispatch import (
    ActivationAuthority,
    GatewayCommand,
    GatewayRuntimeBinding,
    gateway_command_permitted,
)
from .transport import SanitizedTransportResult


GatewaySender = Callable[[dict[str, object]], SanitizedTransportResult | str]


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("GATEWAY_AUTHORITY_INVALID")
    return value


def _iso_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("GATEWAY_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("GATEWAY_TIMESTAMP_INVALID") from error
    if parsed.tzinfo is None:
        raise ValueError("GATEWAY_TIMESTAMP_INVALID")
    return parsed.astimezone(UTC)


class PostgresGatewayDispatcher:
    """One-command runtime; process identity is authenticated by its DB role."""

    def __init__(self, database_url: str, *, runtime: GatewayRuntimeBinding) -> None:
        self._database_url = database_url
        self._runtime = runtime

    @staticmethod
    def _receipt_id(command_id: str, status: str) -> str:
        return canonical_digest(["woozoo.testnet-gateway-receipt/v1", command_id, status])

    def _record_receipt(
        self,
        connection: psycopg.Connection[dict[str, object]],
        *,
        command_id: str,
        request_digest: str,
        client_order_id: str,
        status: str,
        external_effect_count: int,
        now: datetime,
    ) -> dict[str, object]:
        receipt = {
            "command_id": command_id,
            "request_digest": request_digest,
            "client_order_id": client_order_id,
            "status": status,
            "external_effect_count": external_effect_count,
        }
        connection.execute(
            "INSERT INTO testnet_gateway_receipts(receipt_id,command_id,request_digest,status,"
            "external_effect_count,receipt,observed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                self._receipt_id(command_id, status),
                command_id,
                request_digest,
                status,
                external_effect_count,
                Jsonb(receipt),
                now,
            ),
        )
        return receipt

    def _record_observation(
        self,
        connection: psycopg.Connection[dict[str, object]],
        *,
        command_id: str,
        generation_id: str,
        account_binding_id: str,
        account_generation: int,
        client_order_id: str | None,
        observation: dict[str, object],
        now: datetime,
        source_channel: str = "REST",
    ) -> str:
        closed = {
            **observation,
            "environment": "BINANCE_SPOT_TESTNET",
            "account_binding_id": account_binding_id,
            "account_generation": account_generation,
            "generation_id": generation_id,
            "command_id": command_id,
            "client_order_id": client_order_id,
        }
        payload_hash = canonical_digest(cast(CanonicalValue, closed))
        semantic_key = canonical_digest(
            [
                "woozoo.testnet-gateway-observation-semantic-key/v1",
                generation_id,
                command_id,
                payload_hash,
            ]
        )
        observation_id = canonical_digest(
            ["woozoo.testnet-gateway-observation/v1", semantic_key, payload_hash]
        )
        event_time: datetime | None = None
        raw_time = closed.get("event_time_ms")
        if isinstance(raw_time, int) and not isinstance(raw_time, bool) and raw_time > 0:
            event_time = datetime.fromtimestamp(raw_time / 1000, tz=UTC)
        try:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO testnet_gateway_observations(observation_id,generation_id,"
                    "semantic_key,payload_hash,source_channel,client_order_id,event_time,"
                    "received_at,observation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        observation_id,
                        generation_id,
                        semantic_key,
                        payload_hash,
                        source_channel,
                        client_order_id,
                        event_time,
                        now,
                        Jsonb(closed),
                    ),
                )
        except psycopg.errors.UniqueViolation:
            # The identity is content-addressed. A duplicate carries no new effect.
            pass
        return observation_id

    def run_once(
        self, sender: GatewaySender, *, now: datetime | None = None
    ) -> dict[str, object] | None:
        authority_at = (now or datetime.now(UTC)).astimezone(UTC)
        connection = psycopg.connect(self._database_url, row_factory=dict_row)
        lock_key: str | None = None
        try:
            unresolved = connection.execute(
                "SELECT * FROM testnet_gateway_unresolved_dispatch_v1 "
                "ORDER BY started_at,command_id LIMIT 1"
            ).fetchone()
            if unresolved is not None:
                lock_key = str(unresolved["command_id"])
                acquired = connection.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (lock_key,)
                ).fetchone()
                if acquired is None or acquired["pg_try_advisory_lock"] is not True:
                    return None
                result = self._record_receipt(
                    connection,
                    command_id=lock_key,
                    request_digest=str(unresolved["request_digest"]),
                    client_order_id=str(unresolved["client_order_id"]),
                    status="SUBMISSION_UNKNOWN",
                    external_effect_count=1,
                    now=authority_at,
                )
                connection.commit()
                return result
            row = connection.execute(
                "SELECT * FROM testnet_pending_gateway_commands_v1 "
                "ORDER BY issued_at,command_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            lock_key = str(row["command_id"])
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (lock_key,)
            ).fetchone()
            if acquired is None or acquired["pg_try_advisory_lock"] is not True:
                return None
            prior = connection.execute(
                "SELECT status,external_effect_count FROM testnet_gateway_receipt_reader_v1 "
                "WHERE command_id=%s ORDER BY observed_at DESC LIMIT 1",
                (lock_key,),
            ).fetchone()
            if prior is not None:
                return dict(prior)
            authority = connection.execute("SELECT * FROM testnet_gateway_authority_v1").fetchone()
            payload = cast(dict[str, object], row["command"])
            command = GatewayCommand(
                command_id=lock_key,
                request_digest=str(row["request_digest"]),
                command_type=str(row["command_type"]),
                effect_class=str(row["effect_class"]),
                environment=str(payload.get("environment", "")),
                account_generation=_integer(payload.get("account_generation")),
                client_order_id=str(row["client_order_id"]),
                configuration_digest=str(payload.get("gateway_configuration_digest", "")),
                allowlist_digest=str(payload.get("gateway_allowlist_digest", "")),
            )
            activation = ActivationAuthority(
                deployment_enabled=True,
                operator_active=(
                    authority is not None
                    and authority["activation_status"] == "ACTIVE"
                    and isinstance(authority["activation_expires_at"], datetime)
                    and authority["activation_expires_at"] > authority_at
                ),
                environment=str(authority["environment"] if authority else ""),
                account_generation=_integer(
                    authority["generation_number"] if authority is not None else None
                ),
                gateway_instance_id=str(authority["gateway_instance_id"] if authority else ""),
                build_digest=str(authority["build_digest"] if authority else ""),
                configuration_digest=str(authority["configuration_digest"] if authority else ""),
                allowlist_digest=str(authority["allowlist_digest"] if authority else ""),
                gateway_health=(
                    "UNKNOWN"
                    if authority is None
                    else "KILLED"
                    if authority["paper_kill_active"] is not False
                    else "READY"
                    if authority["generation_status"] == "ACTIVE"
                    and authority["reconciliation_status"] == "HEALTHY"
                    and authority["testnet_barrier_active"] is False
                    else "ACTIVATING"
                    if authority["generation_status"] == "PENDING_RECONCILIATION"
                    else "DEGRADED"
                ),
                paper_kill_active=(
                    authority is None or authority["paper_kill_active"] is not False
                ),
                testnet_barrier_active=(
                    authority is None or authority["testnet_barrier_active"] is not False
                ),
                reconciliation_status=str(
                    authority["reconciliation_status"] if authority else "UNKNOWN"
                ),
            )
            try:
                timestamp_matches = (
                    _iso_timestamp(payload.get("issued_at")) <= authority_at
                    and _iso_timestamp(payload.get("expires_at")) > authority_at
                )
            except ValueError:
                timestamp_matches = False
            cancel_authority_matches = True
            if command.command_type == "CANCEL_EXISTING_ORDER":
                cancel_authority_id = row["cancel_authorization_id"]
                cancel_issued_at = row["cancel_issued_at"]
                cancel_expires_at = row["cancel_expires_at"]
                cancel_consumed_at = row["cancel_consumed_at"]
                cancel_order_digest = row["cancel_order_digest"]
                cancel_input_digest = row["cancel_authorization_input_digest"]
                cancel_authority_matches = (
                    row["authorization_id"] is None
                    and isinstance(cancel_authority_id, str)
                    and len(cancel_authority_id) == 64
                    and row["cancel_client_order_id"] == command.client_order_id
                    and row["cancel_generation_id"] == row["generation_id"]
                    and isinstance(row["cancel_order_version"], int)
                    and row["cancel_order_version"] >= 1
                    and isinstance(cancel_issued_at, datetime)
                    and isinstance(cancel_expires_at, datetime)
                    and isinstance(cancel_consumed_at, datetime)
                    and cancel_issued_at <= cancel_consumed_at <= authority_at
                    and cancel_expires_at > authority_at
                    and payload.get("causation_id") == cancel_authority_id
                    and row["request_digest"]
                    == canonical_digest(
                        [
                            "woozoo.testnet-cancel-request/v1",
                            command.command_id,
                            cancel_authority_id,
                            str(cancel_order_digest),
                            str(cancel_input_digest),
                        ]
                    )
                )
            else:
                cancel_authority_matches = (
                    row["authorization_id"] is not None
                    and row["cancel_authorization_id"] is None
                    and payload.get("original_authorization_id") == row["authorization_id"]
                )
            payload_identity_matches = (
                payload.get("schema_version") == "woozoo.testnet-gateway-command/v1"
                and payload.get("command_id") == command.command_id
                and payload.get("request_digest") == command.request_digest
                and payload.get("command_type") == command.command_type
                and payload.get("effect_class") == command.effect_class
                and payload.get("client_order_id") == command.client_order_id
                and payload.get("account_generation") == command.account_generation
                and payload.get("account_binding_id")
                == (authority["account_binding_id"] if authority is not None else None)
                and payload.get("capability_id") == row["capability_id"]
                and cancel_authority_matches
                and payload.get("original_approval_id") == row["approval_id"]
                and payload.get("activation_version")
                == (authority["activation_version"] if authority is not None else None)
                and payload.get("testnet_barrier_version")
                == (authority["barrier_version"] if authority is not None else None)
                and payload.get("gateway_configuration_digest")
                == self._runtime.configuration_digest
                and payload.get("gateway_allowlist_digest") == self._runtime.allowlist_digest
                and timestamp_matches
            )
            connection.execute(
                "INSERT INTO testnet_gateway_inbox(command_id,request_digest,received_at) "
                "VALUES (%s,%s,%s)",
                (command.command_id, command.request_digest, authority_at),
            )
            if not payload_identity_matches or not gateway_command_permitted(
                command, activation, self._runtime
            ):
                result = self._record_receipt(
                    connection,
                    command_id=command.command_id,
                    request_digest=command.request_digest,
                    client_order_id=command.client_order_id,
                    status="REJECTED_LOCAL",
                    external_effect_count=0,
                    now=authority_at,
                )
                connection.commit()
                return result
            attempt_id = canonical_digest(
                ["woozoo.testnet-dispatch-attempt/v1", command.command_id]
            )
            connection.execute(
                "INSERT INTO testnet_gateway_dispatch_attempts(attempt_id,command_id,"
                "client_order_id,status,started_at) VALUES (%s,%s,%s,'DISPATCH_RECORDED',%s)",
                (attempt_id, command.command_id, command.client_order_id, authority_at),
            )
            self._record_receipt(
                connection,
                command_id=command.command_id,
                request_digest=command.request_digest,
                client_order_id=command.client_order_id,
                status="DISPATCH_RECORDED",
                external_effect_count=0,
                now=authority_at,
            )
            connection.commit()
            try:
                transport_result = sender(payload)
            except Exception:
                transport_result = "SUBMISSION_UNKNOWN"
            raw_status = (
                transport_result.status
                if isinstance(transport_result, SanitizedTransportResult)
                else transport_result
            )
            status = (
                raw_status
                if raw_status in {"EXCHANGE_ACKNOWLEDGED", "RESOLVED_REJECTED"}
                else "SUBMISSION_UNKNOWN"
            )
            result = self._record_receipt(
                connection,
                command_id=command.command_id,
                request_digest=command.request_digest,
                client_order_id=command.client_order_id,
                status=status,
                external_effect_count=1,
                now=max(datetime.now(UTC), authority_at) + timedelta(microseconds=1),
            )
            if (
                isinstance(transport_result, SanitizedTransportResult)
                and transport_result.observation is not None
                and authority is not None
            ):
                result["observation_id"] = self._record_observation(
                    connection,
                    command_id=command.command_id,
                    generation_id=str(authority["generation_id"]),
                    account_binding_id=str(authority["account_binding_id"]),
                    account_generation=_integer(authority["generation_number"]),
                    client_order_id=command.client_order_id,
                    observation=transport_result.observation,
                    now=max(datetime.now(UTC), authority_at) + timedelta(microseconds=1),
                )
            connection.commit()
            return result
        finally:
            if lock_key is not None:
                try:
                    connection.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,)
                    )
                except psycopg.Error:
                    pass
            connection.close()


class PostgresGatewayObservationWriter(PostgresGatewayDispatcher):
    """Append one sanitized REST/User Data observation under an active runtime binding."""

    def _authorized(
        self, connection: psycopg.Connection[dict[str, object]], observed_at: datetime
    ) -> dict[str, object]:
        authority = connection.execute("SELECT * FROM testnet_gateway_authority_v1").fetchone()
        if authority is None:
            raise ValueError("GATEWAY_AUTHORITY_MISSING")
        expires_at = authority["activation_expires_at"]
        permitted = (
            authority["environment"] == "BINANCE_SPOT_TESTNET"
            and authority["activation_status"] == "ACTIVE"
            and isinstance(expires_at, datetime)
            and expires_at > observed_at
            and authority["gateway_instance_id"] == self._runtime.gateway_instance_id
            and authority["build_digest"] == self._runtime.build_digest
            and authority["configuration_digest"] == self._runtime.configuration_digest
            and authority["allowlist_digest"] == self._runtime.allowlist_digest
            and authority["generation_status"]
            in {
                "PENDING_RECONCILIATION",
                "ACTIVE",
                "RESET_SUSPECTED",
                "AWAITING_OPERATOR_CONFIRMATION",
            }
        )
        if not permitted:
            raise ValueError("GATEWAY_OBSERVATION_NOT_AUTHORIZED")
        return dict(authority)

    def assert_authorized(self, *, now: datetime | None = None) -> dict[str, object]:
        authority_at = (now or datetime.now(UTC)).astimezone(UTC)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            return self._authorized(connection, authority_at)

    def record(
        self,
        observation: dict[str, object],
        *,
        source_channel: str,
        now: datetime | None = None,
    ) -> str:
        if source_channel not in {"REST", "USER_DATA"}:
            raise ValueError("OBSERVATION_SOURCE_INVALID")
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            authority = self._authorized(connection, observed_at)
            order = observation.get("order")
            client_order_id = (
                str(order.get("client_order_id"))
                if isinstance(order, dict) and order.get("client_order_id") is not None
                else None
            )
            command_id = canonical_digest(
                [
                    "woozoo.testnet-observation-request/v1",
                    str(authority["activation_id"]),
                    str(observation.get("capability_id", "SPOT_TESTNET_USER_DATA")),
                    canonical_digest(cast(CanonicalValue, observation)),
                ]
            )
            observation_id = self._record_observation(
                connection,
                command_id=command_id,
                generation_id=str(authority["generation_id"]),
                account_binding_id=str(authority["account_binding_id"]),
                account_generation=_integer(authority["generation_number"]),
                client_order_id=client_order_id,
                observation=observation,
                now=observed_at,
                source_channel=source_channel,
            )
            connection.commit()
            return observation_id
