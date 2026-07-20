"""A configuration-gated relay skeleton with no domain event producer."""

from __future__ import annotations

import os

from platform_core.config import PlatformSettings


def validate_runtime() -> PlatformSettings:
    return PlatformSettings.from_mapping(os.environ).require_service_dependencies()


def run_once() -> int:
    """Reserve the relay process boundary; Phase 1 never manufactures an event."""
    validate_runtime()
    return 0
