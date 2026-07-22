"""HMAC signing primitive isolated to the Gateway package."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote


def canonical_query(parameters: tuple[tuple[str, str], ...]) -> str:
    if any(not isinstance(name, str) or not isinstance(value, str) for name, value in parameters):
        raise TypeError("query names and values must be strings")
    if len({name for name, _ in parameters}) != len(parameters):
        raise ValueError("DUPLICATE_QUERY_PARAMETER")
    return "&".join(f"{quote(name, safe='')}={quote(value, safe='')}" for name, value in parameters)


def hmac_sha256_signature(secret: bytes, query: str) -> str:
    if not secret or not isinstance(query, str):
        raise ValueError("SIGNING_INPUT_INVALID")
    return hmac.new(secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
