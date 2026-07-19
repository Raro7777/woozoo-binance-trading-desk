"""Closed Decimal policy for the Phase 4 financial authority."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
import re


SCALE = Decimal("0.000000000000000001")
DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
NUMERIC_PRECISION_ERROR = "financial result exceeds NUMERIC(38,18) precision"


def validate_numeric(value: Decimal) -> Decimal:
    """Reject values that cannot be stored exactly as PostgreSQL NUMERIC(38,18)."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("non-finite financial result")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("non-finite financial result")
    if exponent < -18:
        raise ValueError("financial result exceeds NUMERIC(38,18) scale")
    integer_digits = max(len(value.as_tuple().digits) + exponent, 0)
    if integer_digits > 20:
        raise ValueError(NUMERIC_PRECISION_ERROR)
    return value


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
    integer_digits = max(len(number.as_tuple().digits) + exponent, 0)
    if integer_digits > 20:
        raise ValueError("financial input exceeds NUMERIC(38,18) precision")
    return number


def quantize(value: Decimal, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("non-finite financial result")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("non-finite financial result")
    if max(len(value.as_tuple().digits) + exponent, 0) > 20:
        raise ValueError(NUMERIC_PRECISION_ERROR)
    try:
        with localcontext() as context:
            context.prec = max(60, len(value.as_tuple().digits) + max(-exponent, 0) + 2)
            result = value.quantize(SCALE, rounding=rounding)
    except InvalidOperation as error:
        raise ValueError(NUMERIC_PRECISION_ERROR) from error
    return validate_numeric(result)


def add(*values: Decimal, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return quantize(sum(values, Decimal(0)), rounding)


def subtract(value: Decimal, *subtrahends: Decimal, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        result = value
        for subtrahend in subtrahends:
            result -= subtrahend
        return quantize(result, rounding)


def multiply(*values: Decimal, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        result = Decimal(1)
        for value in values:
            result *= value
        return quantize(result, rounding)


def proportion(
    value: Decimal,
    numerator: Decimal,
    denominator: Decimal,
    rounding: str = ROUND_HALF_EVEN,
) -> Decimal:
    if denominator == 0:
        raise ValueError("financial denominator must be non-zero")
    with localcontext() as context:
        context.prec = 80
        return quantize(value * numerator / denominator, rounding)


def round_up(value: Decimal) -> Decimal:
    return quantize(value, ROUND_UP)


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("step must be positive")
    with localcontext() as context:
        context.prec = 80
        return quantize((value / step).to_integral_value(rounding=ROUND_DOWN) * step)


def canonical(value: Decimal) -> str:
    normalized = quantize(value)
    return format(normalized, "f")
