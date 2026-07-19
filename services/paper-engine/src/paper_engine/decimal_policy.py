"""Closed Decimal policy for the Phase 4 financial authority."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
import re


SCALE = Decimal("0.000000000000000001")
DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


def decimal_input(value: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValueError("financial input must be a non-negative plain decimal string")
    number = Decimal(value)
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("financial input is outside the allowed range")
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("financial input must be finite")
    if exponent < -18:
        raise ValueError("financial input exceeds NUMERIC(38,18) scale")
    if len(number.as_tuple().digits) + max(exponent, 0) > 38:
        raise ValueError("financial input exceeds NUMERIC(38,18) precision")
    return number


def quantize(value: Decimal, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    if not value.is_finite():
        raise ValueError("non-finite financial result")
    with localcontext() as context:
        context.prec = 60
        return value.quantize(SCALE, rounding=rounding)


def round_up(value: Decimal) -> Decimal:
    return quantize(value, ROUND_UP)


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("step must be positive")
    return quantize((value / step).to_integral_value(rounding=ROUND_DOWN) * step)


def canonical(value: Decimal) -> str:
    normalized = quantize(value)
    return format(normalized, "f")
