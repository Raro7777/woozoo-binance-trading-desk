"""Small canonical hash helper kept inside the isolated Gateway package."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json


CanonicalValue = (
    str | int | bool | None | Mapping[str, "CanonicalValue"] | Sequence["CanonicalValue"]
)


def _normalize(value: CanonicalValue) -> CanonicalValue:
    if isinstance(value, float):
        raise TypeError("gateway canonical inputs cannot contain floats")
    if isinstance(value, Mapping):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError("unsupported gateway canonical value")


def canonical_digest(value: CanonicalValue) -> str:
    payload = json.dumps(
        _normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
