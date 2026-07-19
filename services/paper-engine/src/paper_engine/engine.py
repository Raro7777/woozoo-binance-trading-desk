"""IO-free deterministic Paper Broker used before Phase 7 ingress activation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from collections.abc import Callable

from .decimal_policy import canonical, decimal_input, floor_step, quantize, round_up
from .models import (
    ExecutionFixture,
    FifoLot,
    Journal,
    LedgerEntry,
    LotConsumption,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
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
        self.command_receipts: dict[str, tuple[str, str]] = {}
        self.observation_effects: set[tuple[str, str]] = set()
        self.outbox: tuple[dict[str, str], ...] = ()
        self.broker_seq = 0

    def _next_seq(self) -> int:
        self.broker_seq += 1
        return self.broker_seq

    def _post(self, journal: Journal) -> None:
        journal.assert_balanced()
        key = _id(journal.business_event_type, journal.business_event_id, journal.journal_kind)
        if key in self.journals:
            if self.journals[key] != journal:
                raise ValueError("LEDGER_IDEMPOTENCY_CONFLICT")
            return
        self.journals[key] = journal

    def seed_balance(self, asset: str, amount_text: str, *, seed_id: str) -> None:
        amount = quantize(decimal_input(amount_text, positive=True))
        if asset not in {"BTC", "ETH", "USDT"}:
            raise ValueError("UNSUPPORTED_ASSET")
        if seed_id in self.command_receipts:
            if self.command_receipts[seed_id][0] != canonical(amount):
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return
        self.available[asset] = self.available.get(asset, Decimal(0)) + amount
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
        self.command_receipts[seed_id] = (canonical(amount), journal.journal_id)

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
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("UNSUPPORTED_SYMBOL")
        quantity = quantize(decimal_input(quantity_text, positive=True))
        price = quantize(decimal_input(limit_price_text, positive=True))
        rule = SYMBOL_RULES[symbol]
        if quantity < rule["min_quantity"] or quantity % rule["step"] != 0:
            raise ValueError("QUANTITY_FILTER_FAILED")
        if price % rule["tick"] != 0:
            raise ValueError("PRICE_FILTER_FAILED")
        if quantity * price < rule["min_notional"]:
            raise ValueError("NOTIONAL_FILTER_FAILED")
        request_hash = _id(
            "create",
            client_order_id,
            authorization.authorization_id,
            symbol,
            side,
            canonical(quantity),
            canonical(price),
            authorization.preview_hash,
        )
        prior = self.command_receipts.get(idempotency_key)
        if prior is not None:
            if prior[0] != request_hash:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return self.orders[prior[1]]
        if authorization.authorization_id in self.authorization_attempts:
            raise ValueError("AUTHORIZATION_ALREADY_ATTEMPTED")
        if any(order.client_order_id == client_order_id for order in self.orders.values()):
            raise ValueError("CLIENT_ORDER_ID_CONFLICT")
        base = symbol.removesuffix("USDT")
        if side == OrderSide.BUY:
            held_asset = "USDT"
            held_amount = round_up(quantity * price) + round_up(quantity * price * FEE_RATE)
        else:
            held_asset = base
            held_amount = quantity
        available = self.available.get(held_asset, Decimal(0))
        if available < held_amount:
            self.authorization_attempts[authorization.authorization_id] = "BLOCKED"
            self.command_receipts[idempotency_key] = (request_hash, "REJECTED:INSUFFICIENT_FUNDS")
            raise ValueError("INSUFFICIENT_FUNDS")
        self.available[held_asset] = available - held_amount
        self.held[held_asset] = self.held.get(held_asset, Decimal(0)) + held_amount
        order_id = _id("order", client_order_id)
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
        self.command_receipts[idempotency_key] = (request_hash, order_id)
        self.outbox += ({"event_type": "paper.order.accepted.v1", "order_id": order_id},)
        return order

    def apply_book_observation(
        self,
        *,
        order_id: str,
        observation_id: str,
        best_bid_text: str,
        best_ask_text: str,
        displayed_quantity_text: str,
    ) -> PaperFill | None:
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
        seq = self._next_seq()
        if order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED}:
            raise ValueError("TERMINAL_ORDER")
        bid = decimal_input(best_bid_text, positive=True)
        ask = decimal_input(best_ask_text, positive=True)
        displayed = decimal_input(displayed_quantity_text)
        eligible = (
            ask <= order.limit_price if order.side == OrderSide.BUY else bid >= order.limit_price
        )
        if not eligible or seq <= order.accepted_broker_seq:
            self.observation_effects.add((order_id, observation_id))
            return None
        remaining = order.quantity - order.filled_quantity
        fill_quantity = min(
            remaining,
            floor_step(displayed * PARTICIPATION_RATE, SYMBOL_RULES[order.symbol]["step"]),
        )
        if fill_quantity <= 0:
            self.observation_effects.add((order_id, observation_id))
            return None
        fill_id = _id("fill", order_id, observation_id)
        principal = quantize(fill_quantity * order.limit_price)
        fee = round_up(principal * FEE_RATE)
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
        self.observation_effects.add((order_id, observation_id))
        return fill

    def _apply_fill(self, order: PaperOrder, fill: PaperFill, principal: Decimal) -> None:
        base = order.symbol.removesuffix("USDT")
        if order.side == OrderSide.BUY:
            debit = principal + fill.fee_amount
            if self.held.get("USDT", Decimal(0)) < debit:
                raise ValueError("HELD_BALANCE_UNDERFLOW")
            self.held["USDT"] -= debit
            self.available[base] = self.available.get(base, Decimal(0)) + fill.quantity
            self.lots += (
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
                LedgerEntry("exchange.clearing", "USDT", debit, Decimal(0)),
                LedgerEntry("paper.cash", "USDT", Decimal(0), debit),
            )
        else:
            if self.held.get(base, Decimal(0)) < fill.quantity:
                raise ValueError("HELD_BALANCE_UNDERFLOW")
            self.held[base] -= fill.quantity
            basis = self._consume_fifo(base, fill.quantity, fill.fill_id)
            proceeds = principal - fill.fee_amount
            self.available["USDT"] = self.available.get("USDT", Decimal(0)) + proceeds
            entries = (
                LedgerEntry("exchange.clearing", base, fill.quantity, Decimal(0)),
                LedgerEntry("paper.asset", base, Decimal(0), fill.quantity),
                LedgerEntry("paper.cash", "USDT", proceeds, Decimal(0)),
                LedgerEntry("paper.realized-cost", "USDT", basis, Decimal(0)),
                LedgerEntry("exchange.clearing", "USDT", Decimal(0), proceeds),
                LedgerEntry("paper.cost-basis", "USDT", Decimal(0), basis),
            )
        journal = Journal(
            _id("journal", fill.fill_id), "paper.fill", fill.fill_id, "PHYSICAL", entries
        )
        self._post(journal)
        cumulative = order.filled_quantity + fill.quantity
        status = (
            OrderStatus.FILLED if cumulative == order.quantity else OrderStatus.PARTIALLY_FILLED
        )
        updated = replace(
            order, status=status, filled_quantity=cumulative, version=order.version + 1
        )
        if status == OrderStatus.FILLED:
            release = max(Decimal(0), self._remaining_hold(updated))
            if release:
                self.held[updated.held_asset] -= release
                self.available[updated.held_asset] = (
                    self.available.get(updated.held_asset, Decimal(0)) + release
                )
            updated = replace(updated, held_amount=updated.held_amount - release)
        self.orders[order.order_id] = updated
        event = (
            "paper.order.filled.v1"
            if status == OrderStatus.FILLED
            else "paper.order.partially-filled.v1"
        )
        self.outbox += ({"event_type": event, "order_id": order.order_id, "fill_id": fill.fill_id},)

    def _remaining_hold(self, order: PaperOrder) -> Decimal:
        if order.side == OrderSide.SELL:
            return order.quantity - order.filled_quantity
        remaining = order.quantity - order.filled_quantity
        return round_up(remaining * order.limit_price) + round_up(
            remaining * order.limit_price * FEE_RATE
        )

    def _lot_remaining(self, lot: FifoLot) -> Decimal:
        used = sum(
            (item.quantity for item in self.consumptions if item.lot_id == lot.lot_id), Decimal(0)
        )
        return lot.acquired_quantity - used

    def _consume_fifo(self, asset: str, quantity: Decimal, source_fill_id: str) -> Decimal:
        needed = quantity
        basis = Decimal(0)
        ordered = sorted(
            (lot for lot in self.lots if lot.asset == asset),
            key=lambda lot: (lot.acquired_at, lot.source_fill_id),
        )
        for lot in ordered:
            remaining = self._lot_remaining(lot)
            take = min(needed, remaining)
            if take <= 0:
                continue
            lot_basis = (
                quantize(lot.quote_cost * take / lot.acquired_quantity)
                if lot.quote_cost
                else Decimal(0)
            )
            self.consumptions += (
                LotConsumption(
                    _id("consume", lot.lot_id, source_fill_id),
                    lot.lot_id,
                    source_fill_id,
                    take,
                    lot_basis,
                ),
            )
            basis += lot_basis
            needed -= take
            if needed == 0:
                break
        if needed != 0:
            raise ValueError("FIFO_POSITION_UNDERFLOW")
        return quantize(basis)

    def cancel(self, order_id: str, *, cancel_id: str) -> PaperOrder:
        order = self.orders[order_id]
        if order.status == OrderStatus.CANCELLED:
            return order
        if order.status == OrderStatus.FILLED:
            raise ValueError("TERMINAL_ORDER")
        release = self._remaining_hold(order)
        if self.held.get(order.held_asset, Decimal(0)) < release:
            raise ValueError("HELD_BALANCE_UNDERFLOW")
        self.held[order.held_asset] -= release
        self.available[order.held_asset] = (
            self.available.get(order.held_asset, Decimal(0)) + release
        )
        updated = replace(
            order,
            status=OrderStatus.CANCELLED,
            held_amount=order.held_amount - release,
            version=order.version + 1,
        )
        self.orders[order_id] = updated
        self.outbox += (
            {
                "event_type": "paper.order.cancelled.v1",
                "order_id": order_id,
                "cancel_id": cancel_id,
            },
        )
        return updated

    def position(self, asset: str) -> Decimal:
        acquired = sum(
            (lot.acquired_quantity for lot in self.lots if lot.asset == asset), Decimal(0)
        )
        consumed = sum(
            (
                item.quantity
                for item in self.consumptions
                if any(lot.lot_id == item.lot_id and lot.asset == asset for lot in self.lots)
            ),
            Decimal(0),
        )
        return quantize(acquired - consumed)

    def realized_pnl(self, asset: str) -> Decimal:
        symbol = f"{asset}USDT"
        total = Decimal(0)
        for fill in self.fills.values():
            order = self.orders[fill.order_id]
            if order.symbol != symbol or order.side != OrderSide.SELL:
                continue
            basis = sum(
                (
                    item.quote_basis
                    for item in self.consumptions
                    if item.source_fill_id == fill.fill_id
                ),
                Decimal(0),
            )
            total += fill.quantity * fill.price - basis - fill.fee_amount
        return quantize(total)

    def unrealized_pnl(self, asset: str, mark_price_text: str) -> Decimal:
        mark = decimal_input(mark_price_text, positive=True)
        remaining_basis = Decimal(0)
        for lot in self.lots:
            if lot.asset != asset:
                continue
            remaining = self._lot_remaining(lot)
            if remaining:
                remaining_basis += lot.quote_cost * remaining / lot.acquired_quantity
        return quantize(self.position(asset) * mark - remaining_basis)

    def reverse_and_replace(
        self,
        *,
        original_key: str,
        correction_id: str,
        replacement_entries: tuple[LedgerEntry, ...],
    ) -> tuple[Journal, Journal]:
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
        reversal.assert_balanced()
        replacement.assert_balanced()
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
