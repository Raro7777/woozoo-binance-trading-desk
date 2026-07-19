"""Redact sensitive-looking values before a platform shell can emit diagnostics."""

from __future__ import annotations

import re
from typing import Any


_SENSITIVE_LABELS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "password",
        "token",
        "secret",
    }
)
_REDACTED = "[REDACTED]"


def _is_sensitive_label(key: object) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    normalized = normalized.casefold().replace("-", "_").replace(" ", "_")
    labels = frozenset(label for label in normalized.split("_") if label)
    return (
        normalized in _SENSITIVE_LABELS
        or bool(_SENSITIVE_LABELS.intersection(labels))
        or {"api", "key"}.issubset(labels)
    )


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_label(key) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value
