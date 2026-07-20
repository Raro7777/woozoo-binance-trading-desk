"""IO-free deterministic Paper Broker used before Phase 7 ingress activation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, ROUND_UP
import hashlib
import json
from collections.abc import Callable

from .decimal_policy import (
    add,
    canonical,
    decimal_input,
    floor_product_to_step,
    multiply,
    proportion,
    quantize,
    subtract,
    validate_numeric,
)
from .models import (
    CommandReceipt,
    ExecutionFixture,
    FifoLot,
    Journal,
    LedgerEntry,
    LotConsumption,
    MarkFixture,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
    UnrealizedPnl,
    validate_opaque_id,
)


FEE_RATE = Decimal("0.001")
PARTICIPATION_RATE = Decimal("0.10")
SYMBOL_RULES = {
    "BTCUSDT": {
        "tick": Decimal("0.01000000"),
        "step": Decimal("0.00001000"),
        "min_quantity": Decimal("0.00001000"),
        "min_notional": Decimal("5.00000000"),
    },
    "ETHUSDT": {
        "tick": Decimal("0.01000000"),
        "step": Decimal("0.00010000"),
        "min_quantity": Decimal("0.00010000"),
        "min_notional": Decimal("5.00000000"),
    },
}


def _id(kind: str, *parts: object) -> str:
    raw = json.dumps([kind, *[str(part) for part in parts]], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class PaperEngine:
    """A deterministic aggregate store with no network or production command ingress."""

    def __init__(self) -> None:
        self.available: dict[str, Decimal] = {}
        self.held: dict[str, Decimal] = {}
        self.orders: dict[str, PaperOrder] = {}
        self.fills: dict[str, PaperFill] = {}
        self.lots: tuple[FifoLot, ...] = ()
        self.consumptions: tuple[LotConsumption, ...] = ()
        self.journals: dict[str, Journal] = {}
        self.authorization_attempts: dict[str, str] = {}
        self.command_receipts: dict[str, CommandReceipt] = {}
        self.observation_effects: set[tuple[str, str]] = set()
        self.observation_budgets: dict[str, tuple[str, Decimal]] = {}
        self.observation_sequences: dict[str, int] = {}
        self.cancel_receipts: dict[str, tuple[str, PaperOrder]] = {}
        self.order_cancel_identity: dict[str, str] = {}
        self.outbox: tuple[dict[str, object], ...] = ()
        self.broker_seq = 0

    def _next_seq(self) -> int:
        self.broker_seq += 1
        return self.broker_seq

    def _post(self, journal: Journal) -> None:
        self._validate_journal(journal)
        key = _id(journal.business_event_type, journal.business_event_id, journal.journal_kind)
        if key in self.journals:
            if self.journals[key] != journal:
                raise ValueError("LEDGER_IDEMPOTENCY_CONFLICT")
            return
        self.journals[key] = journal

    @staticmethod
    def _validate_journal(journal: Journal) -> None:
        for entry in journal.entries:
            validate_numeric(entry.debit)
            validate_numeric(entry.credit)
        journal.assert_balanced()

    def _event(
        self,
        event_type: str,
        aggregate_id: str,
        aggregate_version: int,
        data: dict[str, str],
    ) -> dict[str, object]:
        payload_hash = _id(
            "event-payload",
            event_type,
            aggregate_id,
            aggregate_version,
            json.dumps(data, sort_keys=True, separators=(",", ":")),
        )
        return {
            "spec_version": "woozoo.event/v1",
            "event_id": _id("event", event_type, aggregate_id, aggregate_version),
            "event_type": event_type,
            "event_version": 1,
            "occurred_at": datetime.fromtimestamp(self.broker_seq, UTC).isoformat(),
            "producer": "paper-engine",
            "activation_phase": 7,
            "aggregate_id": aggregate_id,
            "aggregate_version": aggregate_version,
            "payload_hash": payload_hash,
            "data": data,
        }

    def seed_balance(self, asset: str, amount_text: str, *, seed_id: str) -> None:
        validate_opaque_id(seed_id)
        amount = quantize(decimal_input(amount_text, positive=True))
        if asset not in {"BTC", "ETH", "USDT"}:
            raise ValueError("UNSUPPORTED_ASSET")
        if seed_id in self.command_receipts:
            if self.command_receipts[seed_id].request_hash != canonical(amount):
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return
        next_available = add(self.available.get(asset, Decimal(0)), amount)
        self.available[asset] = next_available
        if asset in {"BTC", "ETH"}:
            self.lots += (
                FifoLot(
                    _id("seed-lot", seed_id),
                    asset,
                    amount,
                    Decimal(0),
                    seed_id,
                    datetime(1970, 1, 1, tzinfo=UTC),
                ),
            )
        journal = Journal(
            _id("seed-journal", seed_id),
            "paper.seed",
            seed_id,
            "PHYSICAL",
            (
                LedgerEntry("paper.available", asset, amount, Decimal(0)),
                LedgerEntry("paper.opening-equity", asset, Decimal(0), amount),
            ),
        )
        self._post(journal)
        self.command_receipts[seed_id] = CommandReceipt(canonical(amount), "SEEDED")

    def create_limit_order(
        self,
        *,
        idempotency_key: str,
        client_order_id: str,
        authorization: ExecutionFixture,
        symbol: str,
        side: OrderSide,
        quantity_text: str,
        limit_price_text: str,
    ) -> PaperOrder:
        authorization.validate()
        validate_opaque_id(idempotency_key)
        validate_opaque_id(client_order_id)
        if not isinstance(side, OrderSide):
            raise ValueError("INVALID_ORDER_SIDE")
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("UNSUPPORTED_SYMBOL")
        quantity = quantize(decimal_input(quantity_text, positive=True))
        price = quantize(decimal_input(limit_price_text, positive=True))
        rule = SYMBOL_RULES[symbol]
        if quantity < rule["min_quantity"] or quantity % rule["step"] != 0:
            raise ValueError("QUANTITY_FILTER_FAILED")
        if price % rule["tick"] != 0:
            raise ValueError("PRICE_FILTER_FAILED")
        notional = multiply(quantity, price)
        if notional < rule["min_notional"]:
            raise ValueError("NOTIONAL_FILTER_FAILED")
        request_hash = _id(
            "create",
            client_order_id,
            authorization.authorization_id,
            authorization.authorization_nonce,
            symbol,
            side,
            canonical(quantity),
            canonical(price),
            authorization.preview_hash,
        )
        prior = self.command_receipts.get(idempotency_key)
        if prior is not None:
            if prior.request_hash != request_hash:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            if prior.error_code is not None:
                raise ValueError(prior.error_code)
            if prior.order_id is None:
                raise ValueError("CORRUPT_COMMAND_RECEIPT")
            return self.orders[prior.order_id]
        if authorization.authorization_id in self.authorization_attempts:
            raise ValueError("AUTHORIZATION_ALREADY_ATTEMPTED")
        if any(order.client_order_id == client_order_id for order in self.orders.values()):
            raise ValueError("CLIENT_ORDER_ID_CONFLICT")
        base = symbol.removesuffix("USDT")
        if side == OrderSide.BUY:
            held_asset = "USDT"
            held_amount = add(
                multiply(quantity, price, rounding=ROUND_UP),
                multiply(quantity, price, FEE_RATE, rounding=ROUND_UP),
            )
        else:
            held_asset = base
            held_amount = quantity
        available = self.available.get(held_asset, Decimal(0))
        if available < held_amount:
            self.authorization_attempts[authorization.authorization_id] = "BLOCKED"
            self.command_receipts[idempotency_key] = CommandReceipt(
                request_hash, "REJECTED", error_code="INSUFFICIENT_FUNDS"
            )
            self._next_seq()
            auth_aggregate = _id("authorization", authorization.authorization_id)
            self.outbox += (
                self._event(
                    "paper.authorization.attempted.v1",
                    auth_aggregate,
                    1,
                    {"authorization_id": authorization.authorization_id, "outcome": "BLOCKED"},
                ),
                self._event(
                    "paper.order.rejected.v1",
                    request_hash,
                    1,
                    {"request_hash": request_hash, "reason": "INSUFFICIENT_FUNDS"},
                ),
            )
            raise ValueError("INSUFFICIENT_FUNDS")
        next_available = subtract(available, held_amount)
        next_held = add(self.held.get(held_asset, Decimal(0)), held_amount)
        self.available[held_asset] = next_available
        self.held[held_asset] = next_held
        order_id = _id("order", client_order_id)
        self._post(
            Journal(
                _id("hold-journal", order_id),
                "paper.hold",
                order_id,
                "PHYSICAL",
                (
                    LedgerEntry("paper.held", held_asset, held_amount, Decimal(0)),
                    LedgerEntry("paper.available", held_asset, Decimal(0), held_amount),
                ),
            )
        )
        order = PaperOrder(
            order_id,
            client_order_id,
            authorization.authorization_id,
            symbol,
            side,
            quantity,
            price,
            self._next_seq(),
            OrderStatus.OPEN,
            Decimal(0),
            held_asset,
            held_amount,
            1,
        )
        self.orders[order_id] = order
        self.authorization_attempts[authorization.authorization_id] = "CONSUMED_ORDER_CREATED"
        self.command_receipts[idempotency_key] = CommandReceipt(
            request_hash, "ORDER_CREATED", order_id=order_id
        )
        self.outbox += (
            self._event(
                "paper.authorization.attempted.v1",
                _id("authorization", authorization.authorization_id),
                1,
                {"authorization_id": authorization.authorization_id, "outcome": "CONSUMED"},
            ),
            self._event("paper.order.accepted.v1", order_id, order.version, {"order_id": order_id}),
        )
        return order

    def _assert_canonical_observation_order(
        self,
        order: PaperOrder,
        observation_id: str,
        bid: Decimal,
        ask: Decimal,
    ) -> None:
        order_key = (
            order.accepted_broker_seq,
            order.client_order_id,
            order.order_id,
        )
        has_lower_eligible_order = any(
            candidate.symbol == order.symbol
            and candidate.status not in {OrderStatus.FILLED, OrderStatus.CANCELLED}
            and (candidate.order_id, observation_id) not in self.observation_effects
            and (
                candidate.accepted_broker_seq,
                candidate.client_order_id,
                candidate.order_id,
            )
            < order_key
            and (
                ask <= candidate.limit_price
                if candidate.side == OrderSide.BUY
                else bid >= candidate.limit_price
            )
            for candidate in self.orders.values()
        )
        if has_lower_eligible_order:
            raise ValueError("NON_CANONICAL_OBSERVATION_ORDER")

    def apply_book_observation(
        self,
        *,
        order_id: str,
        observation_id: str,
        best_bid_text: str,
        best_ask_text: str,
        displayed_quantity_text: str,
    ) -> PaperFill | None:
        result = self.atomic(
            lambda: self._apply_book_observation(
                order_id=order_id,
                observation_id=observation_id,
                best_bid_text=best_bid_text,
                best_ask_text=best_ask_text,
                displayed_quantity_text=displayed_quantity_text,
            )
        )
        if result is not None and not isinstance(result, PaperFill):
            raise TypeError("unexpected observation result")
        return result

    def _apply_book_observation(
        self,
        *,
        order_id: str,
        observation_id: str,
        best_bid_text: str,
        best_ask_text: str,
        displayed_quantity_text: str,
    ) -> PaperFill | None:
        validate_opaque_id(observation_id)
        order = self.orders[order_id]
        if (order_id, observation_id) in self.observation_effects:
            return next(
                (
                    fill
                    for fill in self.fills.values()
                    if fill.observation_id == observation_id and fill.order_id == order_id
                ),
                None,
            )
        if order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED}:
            raise ValueError("TERMINAL_ORDER")
        bid = decimal_input(best_bid_text, positive=True)
        ask = decimal_input(best_ask_text, positive=True)
        displayed = decimal_input(displayed_quantity_text)
        observation_hash = _id(
            "observation", order.symbol, canonical(bid), canonical(ask), canonical(displayed)
        )
        eligible = (
            ask <= order.limit_price if order.side == OrderSide.BUY else bid >= order.limit_price
        )
        budget = self.observation_budgets.get(observation_id)
        if budget is None:
            self._assert_canonical_observation_order(order, observation_id, bid, ask)
            remaining_budget = floor_product_to_step(
                displayed,
                PARTICIPATION_RATE,
                step=SYMBOL_RULES[order.symbol]["step"],
            )
            self.observation_budgets[observation_id] = (observation_hash, remaining_budget)
            seq = self._next_seq()
            self.observation_sequences[observation_id] = seq
        else:
            if budget[0] != observation_hash:
                raise ValueError("OBSERVATION_ID_CONFLICT")
            remaining_budget = budget[1]
            seq = self.observation_sequences.get(observation_id, 0)
            if seq == 0:
                matching_sequences = {
                    fill.broker_seq
                    for fill in self.fills.values()
                    if fill.observation_id == observation_id
                }
                if len(matching_sequences) != 1:
                    raise ValueError("OBSERVATION_SEQUENCE_MISSING")
                seq = matching_sequences.pop()
                self.observation_sequences[observation_id] = seq
        if seq <= order.accepted_broker_seq:
            return None
        if budget is not None:
            self._assert_canonical_observation_order(order, observation_id, bid, ask)
        if not eligible:
            self.observation_effects.add((order_id, observation_id))
            return None
        remaining = subtract(order.quantity, order.filled_quantity)
        fill_quantity = min(
            remaining,
            remaining_budget,
        )
        if fill_quantity <= 0:
            self.observation_effects.add((order_id, observation_id))
            return None
        fill_id = _id("fill", order_id, observation_id)
        principal = multiply(fill_quantity, order.limit_price)
        fee = multiply(principal, FEE_RATE, rounding=ROUND_UP)
        fill = PaperFill(
            fill_id,
            order_id,
            observation_id,
            fill_quantity,
            order.limit_price,
            "USDT",
            FEE_RATE,
            fee,
            seq,
        )
        self._apply_fill(order, fill, principal)
        self.fills[fill_id] = fill
        self.observation_budgets[observation_id] = (
            observation_hash,
            subtract(remaining_budget, fill_quantity),
        )
        self.observation_effects.add((order_id, observation_id))
        return fill

    def _apply_fill(self, order: PaperOrder, fill: PaperFill, principal: Decimal) -> None:
        base = order.symbol.removesuffix("USDT")
        planned_consumptions: tuple[LotConsumption, ...] = ()
        planned_lots: tuple[FifoLot, ...] = ()
        if order.side == OrderSide.BUY:
            debit = add(principal, fill.fee_amount)
            consumed_hold = debit
            if self.held.get("USDT", Decimal(0)) < debit:
                raise ValueError("HELD_BALANCE_UNDERFLOW")
            next_held = subtract(self.held.get("USDT", Decimal(0)), debit)
            next_received_available = add(self.available.get(base, Decimal(0)), fill.quantity)
            planned_lots = (
                FifoLot(
                    fill.fill_id,
                    base,
                    fill.quantity,
                    debit,
                    fill.fill_id,
                    datetime.fromtimestamp(fill.broker_seq, UTC),
                ),
            )
            entries: tuple[LedgerEntry, ...] = (
                LedgerEntry("paper.asset", base, fill.quantity, Decimal(0)),
                LedgerEntry("exchange.clearing", base, Decimal(0), fill.quantity),
                LedgerEntry("exchange.clearing", "USDT", principal, Decimal(0)),
                LedgerEntry("paper.fee", "USDT", fill.fee_amount, Decimal(0)),
                LedgerEntry("paper.held", "USDT", Decimal(0), debit),
            )
            valuation_entries: tuple[LedgerEntry, ...] = (
                LedgerEntry("paper.inventory-basis", "USDT_VAL", debit, Decimal(0)),
                LedgerEntry("paper.acquisition-value", "USDT_VAL", Decimal(0), debit),
            )
        else:
            consumed_hold = fill.quantity
            if self.held.get(base, Decimal(0)) < fill.quantity:
                raise ValueError("HELD_BALANCE_UNDERFLOW")
            next_held = subtract(self.held.get(base, Decimal(0)), fill.quantity)
            basis, planned_consumptions = self._plan_fifo(base, fill.quantity, fill.fill_id)
            proceeds = subtract(principal, fill.fee_amount)
            next_received_available = add(self.available.get("USDT", Decimal(0)), proceeds)
            entries = (
                LedgerEntry("exchange.clearing", base, fill.quantity, Decimal(0)),
                LedgerEntry("paper.held", base, Decimal(0), fill.quantity),
                LedgerEntry("paper.available", "USDT", proceeds, Decimal(0)),
                LedgerEntry("paper.fee", "USDT", fill.fee_amount, Decimal(0)),
                LedgerEntry("exchange.clearing", "USDT", Decimal(0), principal),
            )
            pnl = subtract(principal, basis, fill.fee_amount)
            pnl_entries: tuple[LedgerEntry, ...] = (
                (LedgerEntry("paper.realized-pnl", "USDT_VAL", Decimal(0), pnl),)
                if pnl > 0
                else (
                    LedgerEntry(
                        "paper.realized-loss",
                        "USDT_VAL",
                        subtract(Decimal(0), pnl),
                        Decimal(0),
                    ),
                )
                if pnl < 0
                else ()
            )
            basis_entries: tuple[LedgerEntry, ...] = (
                (LedgerEntry("paper.inventory-basis", "USDT_VAL", Decimal(0), basis),)
                if basis > 0
                else ()
            )
            valuation_entries = (
                LedgerEntry("paper.disposal-value", "USDT_VAL", principal, Decimal(0)),
                *pnl_entries,
                *basis_entries,
                LedgerEntry("paper.fee-value", "USDT_VAL", Decimal(0), fill.fee_amount),
            )
        journal = Journal(
            _id("journal", fill.fill_id), "paper.fill", fill.fill_id, "PHYSICAL", entries
        )
        valuation_journal = Journal(
            _id("valuation-journal", fill.fill_id),
            "paper.fill",
            fill.fill_id,
            "VALUATION",
            valuation_entries,
        )
        for planned_journal in (journal, valuation_journal):
            self._validate_journal(planned_journal)
        cumulative = add(order.filled_quantity, fill.quantity)
        status = (
            OrderStatus.FILLED if cumulative == order.quantity else OrderStatus.PARTIALLY_FILLED
        )
        residual_hold = subtract(order.held_amount, consumed_hold)
        if residual_hold < 0:
            raise ValueError("ORDER_HOLD_UNDERFLOW")
        updated = replace(
            order,
            status=status,
            filled_quantity=cumulative,
            held_amount=residual_hold,
            version=order.version + 1,
        )
        release = updated.held_amount if status == OrderStatus.FILLED else Decimal(0)
        release_journal = None
        release_available = Decimal(0)
        if release:
            next_held = subtract(next_held, release)
            release_available = add(self.available.get(updated.held_asset, Decimal(0)), release)
            release_journal = self._release_journal(updated.order_id, release, updated.held_asset)
            self._validate_journal(release_journal)
        if order.side == OrderSide.BUY:
            self.held["USDT"] = next_held
            self.available[base] = next_received_available
            self.lots += planned_lots
        else:
            self.held[base] = next_held
            self.available["USDT"] = next_received_available
            self.consumptions += planned_consumptions
        if release:
            self.available[updated.held_asset] = release_available
        self._post(journal)
        self._post(valuation_journal)
        if status == OrderStatus.FILLED:
            if release_journal is not None:
                self._post(release_journal)
            updated = replace(updated, held_amount=Decimal(0))
        self.orders[order.order_id] = updated
        event = (
            "paper.order.filled.v1"
            if status == OrderStatus.FILLED
            else "paper.order.partially-filled.v1"
        )
        self.outbox += (
            self._event(
                event,
                order.order_id,
                updated.version,
                {"order_id": order.order_id, "fill_id": fill.fill_id},
            ),
        )

    def _release_journal(self, order_id: str, amount: Decimal, asset: str) -> Journal:
        return Journal(
            _id("release-journal", order_id),
            "paper.hold-release",
            order_id,
            "PHYSICAL",
            (
                LedgerEntry("paper.available", asset, amount, Decimal(0)),
                LedgerEntry("paper.held", asset, Decimal(0), amount),
            ),
        )

    def _remaining_hold(self, order: PaperOrder) -> Decimal:
        if order.side == OrderSide.SELL:
            return subtract(order.quantity, order.filled_quantity)
        remaining = subtract(order.quantity, order.filled_quantity)
        return add(
            multiply(remaining, order.limit_price, rounding=ROUND_UP),
            multiply(remaining, order.limit_price, FEE_RATE, rounding=ROUND_UP),
        )

    def _lot_remaining(self, lot: FifoLot) -> Decimal:
        used = add(*(item.quantity for item in self.consumptions if item.lot_id == lot.lot_id))
        return subtract(lot.acquired_quantity, used)

    def _lot_consumed_basis(self, lot: FifoLot) -> Decimal:
        return add(*(item.quote_basis for item in self.consumptions if item.lot_id == lot.lot_id))

    def _plan_fifo(
        self, asset: str, quantity: Decimal, source_fill_id: str
    ) -> tuple[Decimal, tuple[LotConsumption, ...]]:
        needed = quantity
        basis = Decimal(0)
        planned: tuple[LotConsumption, ...] = ()
        ordered = sorted(
            (lot for lot in self.lots if lot.asset == asset),
            key=lambda lot: (
                1 if lot.source_fill_id in self.fills else 0,
                self.fills[lot.source_fill_id].broker_seq
                if lot.source_fill_id in self.fills
                else 0,
                lot.source_fill_id,
            ),
        )
        for lot in ordered:
            remaining = self._lot_remaining(lot)
            take = min(needed, remaining)
            if take <= 0:
                continue
            if take == remaining:
                lot_basis = subtract(lot.quote_cost, self._lot_consumed_basis(lot))
            else:
                lot_basis = proportion(lot.quote_cost, take, lot.acquired_quantity)
            planned += (
                LotConsumption(
                    _id("consume", lot.lot_id, source_fill_id),
                    lot.lot_id,
                    source_fill_id,
                    take,
                    lot_basis,
                ),
            )
            basis = add(basis, lot_basis)
            needed = subtract(needed, take)
            if needed == 0:
                break
        if needed != 0:
            raise ValueError("FIFO_POSITION_UNDERFLOW")
        return quantize(basis), planned

    def cancel(self, order_id: str, *, cancel_id: str) -> PaperOrder:
        result = self.atomic(lambda: self._cancel(order_id, cancel_id=cancel_id))
        if not isinstance(result, PaperOrder):
            raise TypeError("unexpected cancel result")
        return result

    def _cancel(self, order_id: str, *, cancel_id: str) -> PaperOrder:
        validate_opaque_id(cancel_id)
        order = self.orders[order_id]
        prior_cancel = self.cancel_receipts.get(cancel_id)
        if prior_cancel is not None:
            if prior_cancel[0] != order_id:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return prior_cancel[1]
        existing_identity = self.order_cancel_identity.get(order_id)
        if existing_identity is not None and existing_identity != cancel_id:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if order.status == OrderStatus.CANCELLED:
            raise ValueError("CORRUPT_CANCEL_RECEIPT")
        if order.status == OrderStatus.FILLED:
            raise ValueError("TERMINAL_ORDER")
        release = order.held_amount
        if self.held.get(order.held_asset, Decimal(0)) < release:
            raise ValueError("HELD_BALANCE_UNDERFLOW")
        next_held = subtract(self.held.get(order.held_asset, Decimal(0)), release)
        next_available = add(self.available.get(order.held_asset, Decimal(0)), release)
        release_journal = self._release_journal(order_id, release, order.held_asset)
        self._validate_journal(release_journal)
        self.held[order.held_asset] = next_held
        self.available[order.held_asset] = next_available
        self._post(release_journal)
        self._next_seq()
        updated = replace(
            order,
            status=OrderStatus.CANCELLED,
            held_amount=subtract(order.held_amount, release),
            version=order.version + 1,
        )
        self.orders[order_id] = updated
        self.cancel_receipts[cancel_id] = (order_id, updated)
        self.order_cancel_identity[order_id] = cancel_id
        self.outbox += (
            self._event(
                "paper.order.cancelled.v1",
                order_id,
                updated.version,
                {"order_id": order_id, "cancel_id": cancel_id},
            ),
        )
        return updated

    def position(self, asset: str) -> Decimal:
        acquired = add(*(lot.acquired_quantity for lot in self.lots if lot.asset == asset))
        consumed = add(
            *(
                item.quantity
                for item in self.consumptions
                if any(lot.lot_id == item.lot_id and lot.asset == asset for lot in self.lots)
            )
        )
        return subtract(acquired, consumed)

    def order_contract(self, order_id: str) -> dict[str, object]:
        order = self.orders[order_id]
        return {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "authorization_id": order.authorization_id,
            "authorization_namespace": order.authorization_namespace,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type,
            "time_in_force": order.time_in_force,
            "quantity": canonical(order.quantity),
            "limit_price": canonical(order.limit_price),
            "filled_quantity": canonical(order.filled_quantity),
            "status": order.status.value,
            "version": order.version,
        }

    def realized_pnl(self, asset: str) -> Decimal:
        symbol = f"{asset}USDT"
        total = Decimal(0)
        for fill in self.fills.values():
            order = self.orders[fill.order_id]
            if order.symbol != symbol or order.side != OrderSide.SELL:
                continue
            basis = add(
                *(
                    item.quote_basis
                    for item in self.consumptions
                    if item.source_fill_id == fill.fill_id
                )
            )
            total = add(
                total,
                subtract(multiply(fill.quantity, fill.price), basis, fill.fee_amount),
            )
        return quantize(total)

    def unrealized_pnl(self, asset: str, mark: MarkFixture) -> UnrealizedPnl:
        mark.validate()
        mark_price = decimal_input(mark.price, positive=True)
        remaining_basis = Decimal(0)
        for lot in self.lots:
            if lot.asset != asset:
                continue
            remaining = self._lot_remaining(lot)
            if remaining:
                remaining_basis = add(
                    remaining_basis,
                    proportion(lot.quote_cost, remaining, lot.acquired_quantity),
                )
        return UnrealizedPnl(
            asset, subtract(multiply(self.position(asset), mark_price), remaining_basis), mark
        )

    def reverse_and_replace(
        self,
        *,
        original_key: str,
        correction_id: str,
        replacement_entries: tuple[LedgerEntry, ...],
    ) -> tuple[Journal, Journal]:
        result = self.atomic(
            lambda: self._reverse_and_replace(
                original_key=original_key,
                correction_id=correction_id,
                replacement_entries=replacement_entries,
            )
        )
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not all(isinstance(item, Journal) for item in result)
        ):
            raise TypeError("unexpected correction result")
        return result

    def _reverse_and_replace(
        self,
        *,
        original_key: str,
        correction_id: str,
        replacement_entries: tuple[LedgerEntry, ...],
    ) -> tuple[Journal, Journal]:
        validate_opaque_id(correction_id)
        original = self.journals[original_key]
        if original.reversal_of is not None:
            raise ValueError("REVERSAL_OF_REVERSAL_FORBIDDEN")
        reversal = Journal(
            _id("reversal", correction_id),
            "paper.correction",
            correction_id,
            original.journal_kind,
            tuple(
                LedgerEntry(entry.account, entry.commodity, entry.credit, entry.debit)
                for entry in original.entries
            ),
            reversal_of=original.journal_id,
        )
        replacement = Journal(
            _id("replacement", correction_id),
            "paper.replacement",
            correction_id,
            original.journal_kind,
            replacement_entries,
            replacement_for=original.journal_id,
        )
        self._validate_journal(reversal)
        self._validate_journal(replacement)
        self._post(reversal)
        self._post(replacement)
        return reversal, replacement

    def semantic_digest(self) -> str:
        payload = {
            "available": {key: canonical(value) for key, value in sorted(self.available.items())},
            "held": {key: canonical(value) for key, value in sorted(self.held.items())},
            "orders": [str(self.orders[key]) for key in sorted(self.orders)],
            "fills": [str(self.fills[key]) for key in sorted(self.fills)],
            "lots": [str(value) for value in self.lots],
            "consumptions": [str(value) for value in self.consumptions],
            "journals": [str(self.journals[key]) for key in sorted(self.journals)],
            "authorization_attempts": sorted(self.authorization_attempts.items()),
            "command_receipts": [
                (key, str(self.command_receipts[key])) for key in sorted(self.command_receipts)
            ],
            "observation_effects": sorted(self.observation_effects),
            "observation_budgets": [
                (key, value[0], canonical(value[1]))
                for key, value in sorted(self.observation_budgets.items())
            ],
            "observation_sequences": sorted(self.observation_sequences.items()),
            "cancel_receipts": [
                (key, value[0], str(value[1]))
                for key, value in sorted(self.cancel_receipts.items())
            ],
            "broker_seq": self.broker_seq,
            "outbox": self.outbox,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def atomic(self, operation: Callable[[], object], *, fail_after: bool = False) -> object:
        snapshot = deepcopy(self.__dict__)
        try:
            result = operation()
            if fail_after:
                raise RuntimeError("INJECTED_COMMIT_FAILURE")
            return result
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(snapshot)
            raise
