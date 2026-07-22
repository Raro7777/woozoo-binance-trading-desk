"""Atomic Testnet observation, order, fill, ledger, and reconciliation authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .canonical import CanonicalValue, canonical_digest
from .commands import build_existing_order_command
from .financial import decimal_value


TERMINAL = frozenset({"FILLED", "CANCELED", "EXPIRED"})
STATUS_RANK = {
    "PENDING_SUBMIT": 0,
    "NEW": 1,
    "PARTIALLY_FILLED": 2,
    "FILLED": 3,
    "CANCELED": 3,
    "EXPIRED": 3,
}


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("TESTNET_OBSERVATION_INTEGER_INVALID")
    return value


class PostgresReconciliationWorker:
    """Process exactly one immutable observation or one UNKNOWN follow-up."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def run_once(self, *, now: datetime | None = None) -> dict[str, object] | None:
        authority_at = (now or datetime.now(UTC)).astimezone(UTC)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row = connection.execute(
                "SELECT observation.*,generation.generation_number,generation.status AS "
                "generation_status FROM testnet_gateway_observations observation "
                "JOIN testnet_account_generations generation USING(generation_id) "
                "LEFT JOIN testnet_observation_inbox inbox USING(observation_id) "
                "WHERE inbox.observation_id IS NULL "
                "ORDER BY observation.received_at,observation.observation_id LIMIT 1"
            ).fetchone()
            if row is not None:
                try:
                    with connection.transaction():
                        return self._process_observation(connection, row, authority_at)
                except (ValueError, psycopg.Error):
                    return self._reject_observation(connection, row, authority_at)
            return self._process_unknown(connection, authority_at)

    def _reject_observation(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        connection.execute(
            "INSERT INTO testnet_observation_inbox(observation_id,payload_hash,processed_at) "
            "VALUES (%s,%s,%s) ON CONFLICT (observation_id) DO NOTHING",
            (row["observation_id"], row["payload_hash"], now),
        )
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "OBSERVATION_REJECTED",
            "observation_id": row["observation_id"],
            "reason": "OBSERVATION_INVARIANT_FAILED",
        }
        event_id = self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.observation.rejected.v1",
            payload=payload,
            now=now,
        )
        self._block(connection, reason="INVARIANT_FAILURE", event_id=event_id, now=now)
        return payload

    def _event(
        self,
        connection: Connection[dict[str, object]],
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        now: datetime,
    ) -> str:
        payload_hash = canonical_digest(cast(CanonicalValue, payload))
        event_id = canonical_digest(
            ["woozoo.testnet-reconciliation-event/v1", aggregate_id, event_type, payload_hash]
        )
        connection.execute(
            "INSERT INTO testnet_domain_events(event_id,aggregate_id,event_type,payload_hash,"
            "payload,occurred_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (event_id, aggregate_id, event_type, payload_hash, Jsonb(payload), now),
        )
        connection.execute(
            "INSERT INTO testnet_outbox(event_id,topic,payload_hash,payload,created_at) "
            "VALUES (%s,'testnet-domain-v1',%s,%s,%s)",
            (event_id, payload_hash, Jsonb(payload), now),
        )
        return event_id

    def _block(
        self,
        connection: Connection[dict[str, object]],
        *,
        reason: str,
        event_id: str,
        now: datetime,
    ) -> None:
        connection.execute(
            "UPDATE testnet_safety_state SET active=true,version=version + "
            "CASE WHEN active AND reason_code=%s THEN 0 ELSE 1 END,last_event_id=%s,"
            "reason_code=%s,updated_at=%s WHERE scope='testnet-global'",
            (reason, event_id, reason, now),
        )

    def _process_unknown(
        self, connection: Connection[dict[str, object]], now: datetime
    ) -> dict[str, object] | None:
        row = connection.execute(
            "SELECT receipt.command_id AS original_command_id,command.authorization_id,"
            "command.approval_id,command.generation_id,command.command,order_row.order_id,"
            "order_row.client_order_id,order_row.external_outcome "
            "FROM testnet_gateway_receipts receipt "
            "JOIN testnet_gateway_commands command USING(command_id) "
            "JOIN testnet_orders order_row USING(command_id) "
            "WHERE receipt.status='SUBMISSION_UNKNOWN' "
            "AND order_row.external_outcome NOT IN ('FOUND','REJECTED','NOT_FOUND_CONFIRMED') "
            "ORDER BY receipt.observed_at,receipt.command_id LIMIT 1 FOR UPDATE OF order_row"
        ).fetchone()
        if row is None:
            return None
        original = dict(cast(dict[str, object], row["command"]))
        query_count = connection.execute(
            "SELECT count(*) AS value FROM testnet_gateway_commands WHERE client_order_id=%s "
            "AND command_type='QUERY_EXISTING_ORDER'",
            (row["client_order_id"],),
        ).fetchone()
        missing_count = connection.execute(
            "SELECT count(*) AS value FROM testnet_observation_inbox inbox "
            "JOIN testnet_gateway_observations observation USING(observation_id) "
            "WHERE observation.client_order_id=%s "
            "AND observation.observation->>'kind'='ORDER_QUERY' "
            "AND observation.observation->>'found'='false' "
            "AND observation.observation->>'authoritative'='true'",
            (row["client_order_id"],),
        ).fetchone()
        queries = _integer(query_count["value"] if query_count else 0)
        missing = _integer(missing_count["value"] if missing_count else 0)
        if row["external_outcome"] != "UNKNOWN_OUTCOME":
            connection.execute(
                "UPDATE testnet_orders SET external_outcome='UNKNOWN_OUTCOME',version=version+1 "
                "WHERE order_id=%s",
                (row["order_id"],),
            )
        if queries > missing or missing >= 2:
            return None
        authority = connection.execute("SELECT * FROM testnet_operator_state_v1").fetchone()
        if authority is None:
            raise ValueError("TESTNET_AUTHORITY_MISSING")
        barrier_increment = not (
            authority["testnet_barrier_active"] is True
            and authority["reason_code"] == "SUBMISSION_UNKNOWN"
        )
        original["activation_version"] = authority["activation_version"]
        original["testnet_barrier_version"] = _integer(authority["barrier_version"]) + int(
            barrier_increment
        )
        query = build_existing_order_command(
            original,
            command_type="QUERY_EXISTING_ORDER",
            sequence=queries + 1,
            issued_at=now,
            query_reason="UNKNOWN_OUTCOME",
        )
        connection.execute(
            "INSERT INTO testnet_gateway_commands(command_id,authorization_id,approval_id,"
            "generation_id,client_order_id,command_type,effect_class,capability_id,"
            "idempotency_key,request_digest,command,issued_at,expires_at) VALUES "
            "(%s,%s,%s,%s,%s,'QUERY_EXISTING_ORDER','OBSERVE_ONLY',"
            "'SPOT_TESTNET_QUERY_BY_CLIENT_ID',%s,%s,%s,%s,%s)",
            (
                query["command_id"],
                row["authorization_id"],
                row["approval_id"],
                row["generation_id"],
                row["client_order_id"],
                query["idempotency_key"],
                query["request_digest"],
                Jsonb(query),
                now,
                datetime.fromisoformat(str(query["expires_at"]).replace("Z", "+00:00")),
            ),
        )
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "UNKNOWN_QUERY_ISSUED",
            "original_command_id": row["original_command_id"],
            "query_command_id": query["command_id"],
            "client_order_id": row["client_order_id"],
            "query_sequence": queries + 1,
        }
        event_id = self._event(
            connection,
            aggregate_id=str(row["order_id"]),
            event_type="testnet.unknown-query.issued.v1",
            payload=payload,
            now=now,
        )
        self._block(connection, reason="SUBMISSION_UNKNOWN", event_id=event_id, now=now)
        return payload

    def _process_observation(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        observation = cast(dict[str, object], row["observation"])
        if canonical_digest(cast(CanonicalValue, observation)) != row["payload_hash"]:
            raise ValueError("TESTNET_OBSERVATION_DIGEST_MISMATCH")
        if observation.get("account_generation") != row["generation_number"]:
            raise ValueError("TESTNET_OBSERVATION_GENERATION_MISMATCH")
        connection.execute(
            "INSERT INTO testnet_observation_inbox(observation_id,payload_hash,processed_at) "
            "VALUES (%s,%s,%s)",
            (row["observation_id"], row["payload_hash"], now),
        )
        kind = observation.get("kind")
        if kind == "SERVER_TIME":
            if (
                observation.get("capability_id") != "SPOT_TESTNET_TIME"
                or observation.get("authoritative") is not True
                or row["source_channel"] != "REST"
                or _integer(observation.get("server_time_ms")) <= 0
            ):
                raise ValueError("TESTNET_SERVER_TIME_OBSERVATION_INVALID")
            payload = {
                "schema_version": "woozoo.testnet-reconciliation-event/v1",
                "outcome": "SERVER_TIME_OBSERVED",
                "observation_id": row["observation_id"],
            }
            self._event(
                connection,
                aggregate_id=str(row["generation_id"]),
                event_type="testnet.server-time.observed.v1",
                payload=payload,
                now=now,
            )
            return payload
        if kind in {"ORDER", "ORDER_QUERY", "USER_DATA_EXECUTION"}:
            if kind == "ORDER_QUERY" and observation.get("found") is False:
                return self._apply_not_found(
                    connection,
                    row,
                    {
                        "client_order_id": row["client_order_id"],
                        "authoritative": observation.get("authoritative"),
                    },
                    now,
                )
            order = observation.get("order")
            if not isinstance(order, dict):
                raise ValueError("TESTNET_ORDER_OBSERVATION_MISSING")
            normalized = dict(order)
            fill = observation.get("fill")
            normalized["fills"] = [fill] if isinstance(fill, dict) else []
            return self._apply_order(connection, row, normalized, now)
        if kind in {"ACCOUNT_SNAPSHOT", "ACCOUNT_DELTA"}:
            return self._apply_account_observation(connection, row, observation, now)
        if kind == "BALANCE_DELTA":
            return self._apply_balance_delta(connection, row, observation, now)
        if kind == "OPEN_ORDERS_SNAPSHOT":
            return self._apply_open_orders_observation(connection, row, observation, now)
        if kind == "TRADE_HISTORY":
            return self._apply_trade_history(connection, row, observation, now)
        if kind in {"USER_DATA_TERMINATED", "USER_DATA_GAP"}:
            payload = {
                "schema_version": "woozoo.testnet-reconciliation-event/v1",
                "outcome": "USER_DATA_GAP",
                "observation_id": row["observation_id"],
            }
            event_id = self._event(
                connection,
                aggregate_id=str(row["generation_id"]),
                event_type="testnet.user-data.terminated.v1",
                payload=payload,
                now=now,
            )
            self._block(connection, reason="USER_DATA_GAP", event_id=event_id, now=now)
            return payload
        if kind == "RESET_SIGNAL":
            return self._apply_snapshot(
                connection,
                row,
                {
                    "complete": True,
                    "balances": self._current_balances(connection, row["generation_id"]),
                    "open_client_order_ids": [],
                    "reset_detected": True,
                    "watermark_at": now.isoformat().replace("+00:00", "Z"),
                },
                now,
            )
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "OBSERVATION_REJECTED",
            "observation_id": row["observation_id"],
            "reason": "OBSERVATION_KIND_INVALID",
        }
        event_id = self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.observation.rejected.v1",
            payload=payload,
            now=now,
        )
        self._block(connection, reason="INVARIANT_FAILURE", event_id=event_id, now=now)
        return payload

    def _apply_balance_delta(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        asset = str(observation.get("asset", ""))
        try:
            delta = Decimal(str(observation.get("delta", "")))
        except InvalidOperation as error:
            raise ValueError("TESTNET_BALANCE_DELTA_INVALID") from error
        if not delta.is_finite() or not asset.isalnum() or asset.upper() != asset:
            raise ValueError("TESTNET_BALANCE_DELTA_INVALID")
        balance = connection.execute(
            "SELECT free FROM testnet_asset_balances WHERE generation_id=%s AND asset=%s "
            "FOR UPDATE",
            (row["generation_id"], asset),
        ).fetchone()
        if balance is None or Decimal(str(balance["free"])) + delta < 0:
            raise ValueError("TESTNET_BALANCE_DELTA_UNEXPLAINED")
        connection.execute(
            "UPDATE testnet_asset_balances SET free=free+%s,source_observation_id=%s,"
            "version=version+1,updated_at=%s WHERE generation_id=%s AND asset=%s",
            (delta, row["observation_id"], now, row["generation_id"], asset),
        )
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "BALANCE_DELTA_APPLIED",
            "observation_id": row["observation_id"],
            "asset": asset,
        }
        self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.balance-delta.observed.v1",
            payload=payload,
            now=now,
        )
        return payload

    def _apply_trade_history(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        if observation.get("gap_detected") is True or observation.get("complete") is False:
            raise ValueError("TESTNET_TRADE_HISTORY_GAP")
        observed_trade_ids = {
            str(cast(dict[str, object], item).get("external_trade_id", ""))
            for item in cast(list[object], observation.get("fills", []))
        }
        observed_exchange_order_ids = {
            str(cast(dict[str, object], item).get("exchange_order_id", ""))
            for item in cast(list[object], observation.get("fills", []))
        }
        if observed_exchange_order_ids:
            known_trade_ids = {
                str(item["external_trade_id"])
                for item in connection.execute(
                    "SELECT fill.external_trade_id FROM testnet_fills fill "
                    "JOIN testnet_orders order_row USING(order_id) "
                    "WHERE fill.generation_id=%s AND order_row.exchange_order_id=ANY(%s)",
                    (row["generation_id"], list(observed_exchange_order_ids)),
                ).fetchall()
            }
            if not known_trade_ids.issubset(observed_trade_ids):
                raise ValueError("TESTNET_TRADE_HISTORY_GAP")
        new_fill_count = 0
        for raw_fill in cast(list[object], observation.get("fills", [])):
            fill = cast(dict[str, object], raw_fill)
            exchange_order_id = str(fill.get("exchange_order_id", ""))
            identity = connection.execute(
                "SELECT order_row.* FROM testnet_gateway_observations item "
                "JOIN testnet_orders order_row ON order_row.generation_id=item.generation_id "
                "AND order_row.client_order_id=item.observation->'order'->>'client_order_id' "
                "WHERE item.generation_id=%s "
                "AND item.observation->'order'->>'exchange_order_id'=%s "
                "ORDER BY item.received_at DESC LIMIT 1",
                (row["generation_id"], exchange_order_id),
            ).fetchone()
            if identity is None:
                raise ValueError("UNEXPLAINED_TESTNET_FILL")
            if self._post_fill(connection, identity, fill, row, now):
                new_fill_count += 1
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "TRADE_HISTORY_APPLIED",
            "observation_id": row["observation_id"],
            "new_fill_count": new_fill_count,
        }
        self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.trade-history.observed.v1",
            payload=payload,
            now=now,
        )
        return payload

    def _current_balances(
        self, connection: Connection[dict[str, object]], generation_id: object
    ) -> list[dict[str, object]]:
        return [
            {"asset": item["asset"], "free": str(item["free"]), "locked": str(item["locked"])}
            for item in connection.execute(
                "SELECT asset,free,locked FROM testnet_asset_balances WHERE generation_id=%s "
                "ORDER BY asset",
                (generation_id,),
            ).fetchall()
        ]

    def _apply_account_observation(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        balances = cast(list[object], observation.get("balances", []))
        if not balances:
            raise ValueError("TESTNET_BALANCES_MISSING")
        previous = connection.execute(
            "SELECT item.observation,item.received_at FROM testnet_gateway_observations item "
            "JOIN testnet_observation_inbox inbox USING(observation_id) "
            "WHERE item.generation_id=%s AND item.observation_id<>%s "
            "AND item.observation->>'kind'='ACCOUNT_SNAPSHOT' "
            "ORDER BY item.received_at DESC,item.observation_id DESC LIMIT 1",
            (row["generation_id"], row["observation_id"]),
        ).fetchone()
        if previous is not None and row["generation_status"] == "ACTIVE":
            previous_observation = cast(dict[str, object], previous["observation"])
            previous_totals = {
                str(item["asset"]): decimal_value(str(item["free"]), allow_zero=True)
                + decimal_value(str(item["locked"]), allow_zero=True)
                for item in cast(list[dict[str, object]], previous_observation.get("balances", []))
            }
            current_totals = {
                str(item["asset"]): decimal_value(str(item["free"]), allow_zero=True)
                + decimal_value(str(item["locked"]), allow_zero=True)
                for item in cast(list[dict[str, object]], balances)
            }
            ledger_deltas = {
                str(item["commodity"]): Decimal(str(item["value"]))
                for item in connection.execute(
                    "SELECT entry.commodity,COALESCE(sum(entry.debit-entry.credit),0) AS value "
                    "FROM testnet_ledger_transactions tx JOIN testnet_ledger_entries entry "
                    "USING(transaction_id) WHERE tx.generation_id=%s AND tx.posted_at>%s "
                    "AND entry.ledger_account LIKE 'asset:%%:available' GROUP BY entry.commodity",
                    (row["generation_id"], previous["received_at"]),
                ).fetchall()
            }
            external_deltas = {
                str(item["asset"]): Decimal(str(item["value"]))
                for item in connection.execute(
                    "SELECT observation->>'asset' AS asset,"
                    "COALESCE(sum((observation->>'delta')::numeric),0) AS value "
                    "FROM testnet_gateway_observations item JOIN testnet_observation_inbox inbox "
                    "USING(observation_id) WHERE item.generation_id=%s AND item.received_at>%s "
                    "AND observation->>'kind'='BALANCE_DELTA' GROUP BY observation->>'asset'",
                    (row["generation_id"], previous["received_at"]),
                ).fetchall()
            }
            assets = (
                set(previous_totals)
                | set(current_totals)
                | set(ledger_deltas)
                | set(external_deltas)
            )
            unexplained = any(
                current_totals.get(asset, Decimal(0)) - previous_totals.get(asset, Decimal(0))
                != ledger_deltas.get(asset, Decimal(0)) + external_deltas.get(asset, Decimal(0))
                for asset in assets
            )
            if unexplained:
                return self._apply_snapshot(
                    connection,
                    row,
                    {
                        "complete": True,
                        "balances": balances,
                        "open_client_order_ids": [],
                        "reset_detected": True,
                        "mismatch_codes": ["BALANCE_DISCONTINUITY"],
                        "watermark_at": now.isoformat().replace("+00:00", "Z"),
                    },
                    now,
                )
        self._upsert_balances(connection, row, balances, now)
        if observation.get("kind") == "ACCOUNT_SNAPSHOT":
            reconciled = self._maybe_reconcile(connection, row, now)
            if reconciled is not None:
                return reconciled
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "ACCOUNT_OBSERVATION_APPLIED",
            "observation_id": row["observation_id"],
        }
        self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.account.observed.v1",
            payload=payload,
            now=now,
        )
        return payload

    def _apply_open_orders_observation(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        orders = cast(list[object], observation.get("orders", []))
        for raw_order in orders:
            order = cast(dict[str, object], raw_order)
            client_order_id = str(order.get("client_order_id", ""))
            known = connection.execute(
                "SELECT 1 FROM testnet_orders WHERE generation_id=%s AND client_order_id=%s",
                (row["generation_id"], client_order_id),
            ).fetchone()
            if known is None:
                raise ValueError("UNEXPLAINED_TESTNET_ORDER")
        reconciled = self._maybe_reconcile(connection, row, now)
        if reconciled is not None:
            return reconciled
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "OPEN_ORDERS_OBSERVATION_APPLIED",
            "observation_id": row["observation_id"],
            "symbol": observation.get("requested_symbol"),
        }
        self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.open-orders.observed.v1",
            payload=payload,
            now=now,
        )
        return payload

    def _maybe_reconcile(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        now: datetime,
    ) -> dict[str, object] | None:
        latest: dict[str, dict[str, object]] = {}
        latest_checkpoint = connection.execute(
            "SELECT created_at FROM testnet_reconciliation_checkpoints "
            "WHERE generation_id=%s ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
            (row["generation_id"],),
        ).fetchone()
        cycle_floor = (
            cast(datetime, latest_checkpoint["created_at"])
            if latest_checkpoint is not None
            else None
        )
        for item in connection.execute(
            "SELECT DISTINCT ON (observation->>'kind',"
            "COALESCE(observation->>'requested_symbol','')) observation,received_at,observation_id "
            "FROM testnet_gateway_observations item JOIN testnet_observation_inbox inbox "
            "USING(observation_id) WHERE item.generation_id=%s "
            "AND observation->>'kind' IN ('ACCOUNT_SNAPSHOT','OPEN_ORDERS_SNAPSHOT') "
            "ORDER BY observation->>'kind',COALESCE(observation->>'requested_symbol',''),"
            "item.received_at DESC,item.observation_id DESC",
            (row["generation_id"],),
        ).fetchall():
            received_at = cast(datetime, item["received_at"])
            if cycle_floor is not None and received_at <= cycle_floor:
                continue
            value = dict(cast(dict[str, object], item["observation"]))
            value["_received_at"] = received_at
            value["_observation_id"] = item["observation_id"]
            key = str(value.get("requested_symbol", value.get("kind", "")))
            latest[key] = value
        if not {"ACCOUNT_SNAPSHOT", "BTCUSDT", "ETHUSDT"}.issubset(latest):
            return None
        received = [cast(datetime, latest[key]["_received_at"]) for key in latest]
        mismatch_codes: list[str] = []
        if any(value > now or now - value > timedelta(seconds=60) for value in received):
            mismatch_codes.append("STALE_SNAPSHOT")
        if max(received) - min(received) > timedelta(seconds=30):
            mismatch_codes.append("SNAPSHOT_CYCLE_MISMATCH")
        open_ids = sorted(
            {
                str(order["client_order_id"])
                for symbol in ("BTCUSDT", "ETHUSDT")
                for order in cast(list[dict[str, object]], latest[symbol].get("orders", []))
            }
        )
        account = latest["ACCOUNT_SNAPSHOT"]
        return self._apply_snapshot(
            connection,
            row,
            {
                "complete": True,
                "balances": account["balances"],
                "open_client_order_ids": open_ids,
                "reset_detected": False,
                "mismatch_codes": mismatch_codes,
                "watermark_at": min(received).isoformat().replace("+00:00", "Z"),
            },
            now,
        )

    def _upsert_balances(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        balances: list[object],
        now: datetime,
    ) -> None:
        seen_assets: set[str] = set()
        for raw_balance in balances:
            balance = cast(dict[str, object], raw_balance)
            asset = str(balance.get("asset", ""))
            free = decimal_value(str(balance.get("free", "")), allow_zero=True)
            locked = decimal_value(str(balance.get("locked", "")), allow_zero=True)
            if not asset.isalnum() or asset.upper() != asset or asset in seen_assets:
                raise ValueError("TESTNET_BALANCE_IDENTITY_INVALID")
            seen_assets.add(asset)
            connection.execute(
                "INSERT INTO testnet_asset_balances(generation_id,asset,free,locked,"
                "source_observation_id,version,updated_at) VALUES (%s,%s,%s,%s,%s,1,%s) "
                "ON CONFLICT (generation_id,asset) DO UPDATE SET free=EXCLUDED.free,"
                "locked=EXCLUDED.locked,source_observation_id=EXCLUDED.source_observation_id,"
                "version=testnet_asset_balances.version+1,updated_at=EXCLUDED.updated_at",
                (
                    row["generation_id"],
                    asset,
                    free,
                    locked,
                    row["observation_id"],
                    now,
                ),
            )

    def _apply_not_found(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        client_order_id = str(observation.get("client_order_id", ""))
        order = connection.execute(
            "SELECT * FROM testnet_orders WHERE generation_id=%s AND client_order_id=%s FOR UPDATE",
            (row["generation_id"], client_order_id),
        ).fetchone()
        authoritative = observation.get("authoritative") is True
        if order is None or not authoritative or row["client_order_id"] != client_order_id:
            raise ValueError("TESTNET_NOT_FOUND_OBSERVATION_INVALID")
        count = connection.execute(
            "SELECT count(*) AS value FROM testnet_observation_inbox inbox "
            "JOIN testnet_gateway_observations item USING(observation_id) "
            "WHERE item.generation_id=%s AND item.client_order_id=%s "
            "AND item.observation->>'kind'='ORDER_QUERY' "
            "AND item.observation->>'found'='false' "
            "AND item.observation->>'authoritative'='true'",
            (row["generation_id"], client_order_id),
        ).fetchone()
        absence_count = _integer(count["value"] if count else 0)
        outcome = "SUBMISSION_UNKNOWN"
        if absence_count >= 2:
            connection.execute(
                "UPDATE testnet_orders SET external_outcome='NOT_FOUND_CONFIRMED',"
                "version=version+1 WHERE order_id=%s",
                (order["order_id"],),
            )
            outcome = "NOT_FOUND_CONFIRMED"
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": outcome,
            "observation_id": row["observation_id"],
            "order_id": order["order_id"],
            "client_order_id": client_order_id,
            "authoritative_absence_count": absence_count,
        }
        self._event(
            connection,
            aggregate_id=str(order["order_id"]),
            event_type="testnet.unknown-observed.v1",
            payload=payload,
            now=now,
        )
        return payload

    def _apply_order(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        client_order_id = str(observation.get("client_order_id", ""))
        order = connection.execute(
            "SELECT * FROM testnet_orders WHERE generation_id=%s AND client_order_id=%s FOR UPDATE",
            (row["generation_id"], client_order_id),
        ).fetchone()
        if order is None or row["client_order_id"] != client_order_id:
            raise ValueError("UNEXPLAINED_TESTNET_ORDER")
        exchange_order_id = str(observation.get("exchange_order_id", ""))
        observed_quantity = decimal_value(str(observation.get("quantity", "")))
        observed_limit_price = decimal_value(str(observation.get("limit_price", "")))
        if (
            not exchange_order_id
            or observation.get("symbol") != order["symbol"]
            or observation.get("side") != order["side"]
            or observed_quantity != Decimal(str(order["quantity"]))
            or observed_limit_price != Decimal(str(order["limit_price"]))
            or (
                order["exchange_order_id"] is not None
                and str(order["exchange_order_id"]) != exchange_order_id
            )
        ):
            raise ValueError("TESTNET_ORDER_IDENTITY_MISMATCH")
        status = str(observation.get("status", ""))
        if status not in STATUS_RANK:
            raise ValueError("TESTNET_ORDER_STATUS_INVALID")
        cumulative = decimal_value(
            str(observation.get("cumulative_filled_quantity", "")), allow_zero=True
        )
        quantity = Decimal(str(order["quantity"]))
        prior_filled = Decimal(str(order["filled_quantity"]))
        if cumulative > quantity:
            raise ValueError("TESTNET_ORDER_OVERFILL")
        new_fill_count = 0
        for raw_fill in cast(list[object], observation.get("fills", [])):
            fill = cast(dict[str, object], raw_fill)
            if self._post_fill(connection, order, fill, row, now):
                new_fill_count += 1
        fill_total_row = connection.execute(
            "SELECT COALESCE(sum(quantity),0) AS value FROM testnet_fills WHERE order_id=%s",
            (order["order_id"],),
        ).fetchone()
        fill_total = Decimal(str(fill_total_row["value"] if fill_total_row else 0))
        if fill_total > quantity or fill_total > cumulative:
            raise ValueError("TESTNET_FILL_RECONCILIATION_FAILED")
        next_status = str(order["status"])
        if next_status not in TERMINAL and STATUS_RANK[status] >= STATUS_RANK[next_status]:
            next_status = status
        next_filled = max(prior_filled, cumulative)
        next_version = _integer(order["version"]) + 1
        connection.execute(
            "UPDATE testnet_orders SET exchange_order_id=%s,filled_quantity=%s,status=%s,"
            "external_outcome='FOUND',"
            "version=%s WHERE order_id=%s",
            (exchange_order_id, next_filled, next_status, next_version, order["order_id"]),
        )
        payload_hash = canonical_digest(cast(CanonicalValue, observation))
        event_type = (
            "FILL_OBSERVED"
            if new_fill_count
            else "CANCEL_OBSERVED"
            if status == "CANCELED"
            else "ORDER_OBSERVED"
        )
        event_id = canonical_digest(
            cast(
                CanonicalValue,
                ["woozoo.testnet-order-event/v1", row["observation_id"], order["order_id"]],
            )
        )
        connection.execute(
            "INSERT INTO testnet_order_events(event_id,order_id,observation_id,order_version,"
            "event_type,payload_hash,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                event_id,
                order["order_id"],
                row["observation_id"],
                next_version,
                event_type,
                payload_hash,
                now,
            ),
        )
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": "ORDER_RECONCILED",
            "observation_id": row["observation_id"],
            "order_id": order["order_id"],
            "client_order_id": client_order_id,
            "status": next_status,
            "filled_quantity": format(next_filled, "f"),
            "new_fill_count": new_fill_count,
        }
        self._event(
            connection,
            aggregate_id=str(order["order_id"]),
            event_type="testnet.order.reconciled.v1",
            payload=payload,
            now=now,
        )
        return payload

    def _post_fill(
        self,
        connection: Connection[dict[str, object]],
        order: dict[str, object],
        fill: dict[str, object],
        observation_row: dict[str, object],
        now: datetime,
    ) -> bool:
        trade_id = str(fill.get("external_trade_id", ""))
        quantity = decimal_value(str(fill.get("quantity", "")))
        price = decimal_value(str(fill.get("price", "")))
        fee = decimal_value(str(fill.get("fee_amount", "")), allow_zero=True)
        fee_asset = str(fill.get("fee_asset", ""))
        exchange_order_id = str(fill.get("exchange_order_id", ""))
        if (
            not trade_id
            or not exchange_order_id
            or (
                order.get("exchange_order_id") is not None
                and exchange_order_id != order["exchange_order_id"]
            )
            or fill.get("symbol") != order["symbol"]
            or not fee_asset.isalnum()
            or fee_asset.upper() != fee_asset
        ):
            raise ValueError("TESTNET_FILL_IDENTITY_INVALID")
        fill_id = canonical_digest(
            cast(
                CanonicalValue,
                ["woozoo.testnet-fill/v1", observation_row["generation_id"], trade_id],
            )
        )
        prior = connection.execute(
            "SELECT * FROM testnet_fills WHERE generation_id=%s AND external_trade_id=%s",
            (observation_row["generation_id"], trade_id),
        ).fetchone()
        if prior is not None:
            if (
                Decimal(str(prior["quantity"])) != quantity
                or Decimal(str(prior["price"])) != price
                or Decimal(str(prior["fee_amount"])) != fee
                or prior["fee_asset"] != fee_asset
                or prior["order_id"] != order["order_id"]
            ):
                raise ValueError("TESTNET_FILL_ID_CONFLICT")
            return False
        connection.execute(
            "INSERT INTO testnet_fills(fill_id,order_id,generation_id,external_trade_id,"
            "quantity,price,fee_amount,fee_asset,observed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                fill_id,
                order["order_id"],
                observation_row["generation_id"],
                trade_id,
                quantity,
                price,
                fee,
                fee_asset,
                now,
            ),
        )
        transaction_id = canonical_digest(["woozoo.testnet-fill-journal/v1", fill_id])
        connection.execute(
            "INSERT INTO testnet_ledger_transactions(transaction_id,generation_id,"
            "business_event_id,journal_kind,posted_at) VALUES (%s,%s,%s,'FILL',%s)",
            (transaction_id, observation_row["generation_id"], fill_id, now),
        )
        symbol = str(order["symbol"])
        base_asset = symbol.removesuffix("USDT")
        quote_asset = "USDT"
        notional = quantity * price
        side = str(order["side"])
        lines: list[tuple[str, str, Decimal, Decimal]] = []
        if side == "BUY":
            lines.extend(
                [
                    (f"asset:{base_asset}:available", base_asset, quantity, Decimal(0)),
                    (f"external:{base_asset}:counterparty", base_asset, Decimal(0), quantity),
                    (f"external:{quote_asset}:counterparty", quote_asset, notional, Decimal(0)),
                    (f"asset:{quote_asset}:available", quote_asset, Decimal(0), notional),
                ]
            )
        else:
            lines.extend(
                [
                    (f"external:{base_asset}:counterparty", base_asset, quantity, Decimal(0)),
                    (f"asset:{base_asset}:available", base_asset, Decimal(0), quantity),
                    (f"asset:{quote_asset}:available", quote_asset, notional, Decimal(0)),
                    (f"external:{quote_asset}:counterparty", quote_asset, Decimal(0), notional),
                ]
            )
        if fee > 0:
            lines.extend(
                [
                    (f"expense:{fee_asset}:fees", fee_asset, fee, Decimal(0)),
                    (f"asset:{fee_asset}:available", fee_asset, Decimal(0), fee),
                ]
            )
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO testnet_ledger_entries(transaction_id,line_no,ledger_account,"
                "commodity,debit,credit) VALUES (%s,%s,%s,%s,%s,%s)",
                [
                    (transaction_id, index, account, commodity, debit, credit)
                    for index, (account, commodity, debit, credit) in enumerate(lines, 1)
                ],
            )
        return True

    def _apply_snapshot(
        self,
        connection: Connection[dict[str, object]],
        row: dict[str, object],
        observation: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        if observation.get("complete") is not True:
            raise ValueError("TESTNET_RECONCILIATION_INCOMPLETE")
        balances = cast(list[object], observation.get("balances", []))
        if not balances:
            raise ValueError("TESTNET_BALANCES_MISSING")
        local_open = {
            str(item["client_order_id"])
            for item in connection.execute(
                "SELECT client_order_id FROM testnet_orders WHERE generation_id=%s "
                "AND status NOT IN ('FILLED','CANCELED','EXPIRED') "
                "AND external_outcome<>'NOT_FOUND_CONFIRMED'",
                (row["generation_id"],),
            ).fetchall()
        }
        observed_open = {
            str(item) for item in cast(list[object], observation.get("open_client_order_ids", []))
        }
        mismatch_codes = list(cast(list[str], observation.get("mismatch_codes", [])))
        reset_detected = observation.get("reset_detected") is True
        if reset_detected:
            mismatch_codes.append("RESET_SIGNAL")
        else:
            if local_open - observed_open:
                mismatch_codes.append("KNOWN_ORDER_LOSS")
            if observed_open - local_open:
                mismatch_codes.append("ORDER_MISMATCH")
        balance_rows = connection.execute(
            "SELECT asset,free,locked,version FROM testnet_asset_balances "
            "WHERE generation_id=%s ORDER BY asset",
            (row["generation_id"],),
        ).fetchall()
        ledger_rows = connection.execute(
            "SELECT tx.transaction_id,tx.business_event_id,entry.line_no,entry.ledger_account,"
            "entry.commodity,entry.debit,entry.credit FROM testnet_ledger_transactions tx "
            "JOIN testnet_ledger_entries entry USING(transaction_id) "
            "WHERE tx.generation_id=%s ORDER BY tx.transaction_id,entry.line_no",
            (row["generation_id"],),
        ).fetchall()
        ledger_imbalances = connection.execute(
            "SELECT entry.commodity FROM testnet_ledger_entries entry "
            "JOIN testnet_ledger_transactions tx USING(transaction_id) "
            "WHERE tx.generation_id=%s GROUP BY entry.transaction_id,entry.commodity "
            "HAVING sum(entry.debit)<>sum(entry.credit) LIMIT 1",
            (row["generation_id"],),
        ).fetchone()
        if ledger_imbalances is not None:
            mismatch_codes.append("LEDGER_BALANCE_MISMATCH")
        ledger_snapshot_digest = canonical_digest(
            cast(
                CanonicalValue,
                {
                    "balances": [
                        {
                            key: format(value, "f") if isinstance(value, Decimal) else value
                            for key, value in item.items()
                        }
                        for item in balance_rows
                    ],
                    "journal": [
                        {
                            key: format(value, "f") if isinstance(value, Decimal) else value
                            for key, value in item.items()
                        }
                        for item in ledger_rows
                    ],
                },
            )
        )
        observation_ids = [
            str(item["observation_id"])
            for item in connection.execute(
                "SELECT inbox.observation_id FROM testnet_observation_inbox inbox "
                "JOIN testnet_gateway_observations item USING(observation_id) "
                "WHERE item.generation_id=%s ORDER BY inbox.observation_id",
                (row["generation_id"],),
            ).fetchall()
        ]
        observation_set_digest = canonical_digest(
            ["woozoo.testnet-observation-set/v1", *observation_ids]
        )
        status = (
            "AWAITING_OPERATOR_CONFIRMATION"
            if reset_detected
            else "FAILED"
            if mismatch_codes
            else "HEALTHY"
        )
        version_row = connection.execute(
            "SELECT COALESCE(max(version),0) AS value FROM testnet_reconciliation_checkpoints "
            "WHERE generation_id=%s",
            (row["generation_id"],),
        ).fetchone()
        checkpoint_version = _integer(version_row["value"] if version_row else 0) + 1
        checkpoint_material: dict[str, object] = {
            "schema_version": "woozoo.testnet-reconciliation/v1",
            "generation_id": row["generation_id"],
            "account_generation": row["generation_number"],
            "observation_set_digest": observation_set_digest,
            "ledger_snapshot_digest": ledger_snapshot_digest,
            "status": status,
            "mismatch_codes": mismatch_codes,
            "watermark_at": str(observation.get("watermark_at", "")),
            "version": checkpoint_version,
        }
        checkpoint_digest = canonical_digest(cast(CanonicalValue, checkpoint_material))
        checkpoint_id = canonical_digest(
            ["woozoo.testnet-reconciliation-checkpoint/v1", checkpoint_digest]
        )
        connection.execute(
            "INSERT INTO testnet_reconciliation_checkpoints(checkpoint_id,generation_id,"
            "checkpoint_digest,observation_set_digest,ledger_snapshot_digest,status,"
            "mismatch_codes,operator_confirmation_digest,watermark_at,created_at,version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s)",
            (
                checkpoint_id,
                row["generation_id"],
                checkpoint_digest,
                observation_set_digest,
                ledger_snapshot_digest,
                status,
                mismatch_codes,
                now,
                now,
                checkpoint_version,
            ),
        )
        payload = {
            "schema_version": "woozoo.testnet-reconciliation-event/v1",
            "outcome": status,
            "observation_id": row["observation_id"],
            "checkpoint_id": checkpoint_id,
            "checkpoint_digest": checkpoint_digest,
            "mismatch_codes": mismatch_codes,
        }
        event_id = self._event(
            connection,
            aggregate_id=str(row["generation_id"]),
            event_type="testnet.reconciliation.completed.v1",
            payload=payload,
            now=now,
        )
        if status == "HEALTHY":
            if row["generation_status"] == "PENDING_RECONCILIATION":
                connection.execute(
                    "UPDATE testnet_account_generations SET status='ACTIVE',"
                    "opening_snapshot_digest=%s,version=version+1 WHERE generation_id=%s",
                    (checkpoint_digest, row["generation_id"]),
                )
            activation = connection.execute(
                "SELECT activation.activation_id FROM testnet_activations activation "
                "LEFT JOIN testnet_activation_revocations revocation USING(activation_id) "
                "WHERE activation.generation_id=%s AND activation.status='ACTIVE' "
                "AND activation.expires_at>%s AND revocation.activation_id IS NULL "
                "ORDER BY activation.activated_at DESC LIMIT 1",
                (row["generation_id"], now),
            ).fetchone()
            if activation is not None:
                connection.execute(
                    "UPDATE testnet_safety_state SET active=false,version=version+1,"
                    "last_event_id=%s,updated_at=%s WHERE scope='testnet-global' "
                    "AND active AND reason_code='DEFAULT_OFF'",
                    (event_id, now),
                )
        else:
            next_generation_status = (
                "AWAITING_OPERATOR_CONFIRMATION" if reset_detected else "RESET_SUSPECTED"
            )
            if reset_detected:
                connection.execute(
                    "UPDATE testnet_account_generations SET status=%s,version=version+1 "
                    "WHERE generation_id=%s",
                    (next_generation_status, row["generation_id"]),
                )
            self._block(
                connection,
                reason="RESET_SUSPECTED" if reset_detected else "RECONCILIATION_FAILURE",
                event_id=event_id,
                now=now,
            )
        return payload
