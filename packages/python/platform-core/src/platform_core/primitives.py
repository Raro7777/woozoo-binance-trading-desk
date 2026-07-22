"""Deterministic, dependency-free platform primitives."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import secrets
import time
from typing import Any
from uuid import UUID


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_request_id() -> UUID:
    """Return a UUIDv7 constructed from UTC milliseconds and cryptographic entropy."""
    timestamp_ms = int(time.time() * 1000)
    if timestamp_ms >= 1 << 48:
        raise RuntimeError("UTC millisecond timestamp does not fit UUIDv7")

    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise TypeError("canonical JSON does not accept binary floating point")
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _normalize(value[key]) for key in sorted(value)}
    raise TypeError(f"canonical JSON cannot encode {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
