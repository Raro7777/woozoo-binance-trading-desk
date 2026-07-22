"""Canonical Phase 8 hashes and stable exchange-safe identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal


CanonicalValue = (
    str | int | bool | None | Mapping[str, "CanonicalValue"] | Sequence["CanonicalValue"]
)


def _normalize(value: CanonicalValue) -> CanonicalValue:
    if isinstance(value, float) or isinstance(value, Decimal):
        raise TypeError("financial canonical inputs must be decimal strings")
    if isinstance(value, Mapping):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError("unsupported canonical value")


def canonical_json(value: CanonicalValue) -> str:
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(value: CanonicalValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def derive_client_order_id(
    authorization_digest: str, execution_nonce_hash: str, account_generation: int
) -> str:
    command_id = canonical_digest(
        [
            "woozoo.testnet-order-command/v1",
            authorization_digest,
            execution_nonce_hash,
            account_generation,
        ]
    )
    external_digest = canonical_digest(["woozoo.testnet-client-order-id/v1", command_id])
    return f"wz8-{external_digest[:32]}"
