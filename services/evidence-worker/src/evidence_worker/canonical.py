"""Closed canonical serialization used by feature and Evidence digests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from typing import TypeAlias


CanonicalScalar: TypeAlias = str | int | bool | None
CanonicalValue: TypeAlias = CanonicalScalar | list["CanonicalValue"] | dict[str, "CanonicalValue"]


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("canonical timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def decimal_text(value: Decimal, *, fixed_scale: int | None = None) -> str:
    if not value.is_finite():
        raise ValueError("canonical decimals must be finite")
    if fixed_scale is not None:
        return format(value, f".{fixed_scale}f")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def canonical_json(value: CanonicalValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_digest(value: CanonicalValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
