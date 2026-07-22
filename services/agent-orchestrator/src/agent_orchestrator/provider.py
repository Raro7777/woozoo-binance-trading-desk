"""Tool-free provider boundary and deterministic Mock LLM."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Protocol

from .canonical import canonical_hash
from .models import Role


class ProviderError(RuntimeError):
    """Sanitized provider failure without raw response or secrets."""


class LlmProvider(Protocol):
    name: str
    model: str

    async def invoke(self, role: Role, request: Mapping[str, object]) -> str: ...


class MockLlmProvider:
    """Network-free response generator keyed only by the canonical request."""

    name = "mock"
    model = "woozoo-deterministic-mock/v1"

    def __init__(
        self,
        *,
        overrides: Mapping[Role, str] | None = None,
        delays: Mapping[Role, float] | None = None,
        failures: frozenset[Role] = frozenset(),
    ) -> None:
        self._overrides = dict(overrides or {})
        self._delays = dict(delays or {})
        self._failures = failures

    async def invoke(self, role: Role, request: Mapping[str, object]) -> str:
        await asyncio.sleep(self._delays.get(role, 0.0))
        if role in self._failures:
            raise ProviderError("mock provider failure")
        if role in self._overrides:
            return self._overrides[role]
        evidence_item_ids = request["evidence_item_ids"]
        dependency_report_ids = request["dependency_report_ids"]
        quoted_evidence = request["quoted_evidence"]
        if (
            not isinstance(evidence_item_ids, list)
            or not isinstance(dependency_report_ids, list)
            or not isinstance(quoted_evidence, list)
            or len(quoted_evidence) != len(evidence_item_ids)
            or not all(isinstance(item, str) and item for item in quoted_evidence)
        ):
            raise ProviderError("invalid deterministic mock request")
        evidence_content_digest = canonical_hash(quoted_evidence)
        stance = "BUY" if role in {Role.BULL, Role.TRADER, Role.PORTFOLIO} else "NEUTRAL"
        if role is Role.BEAR:
            stance = "HOLD"
        payload = {
            "role": role.value,
            "evidence_id": request["evidence_id"],
            "evidence_digest": request["evidence_digest"],
            "as_of": request["as_of"],
            "knowledge_cutoff": request["knowledge_cutoff"],
            "symbol": request["symbol"],
            "evidence_item_ids": evidence_item_ids[:1],
            "dependency_report_ids": dependency_report_ids,
            "claim_times": [request["as_of"]],
            "findings": [
                f"{role.value.lower()} finding is bounded to Evidence content "
                f"{evidence_content_digest[:16]}"
            ],
            "uncertainty": ["mock uncertainty"],
            "invalidation_conditions": ["Evidence quality changes"],
            "confidence": "0.5",
            "stance": stance,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
