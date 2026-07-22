"""Fail-closed settings for the isolated Spot Testnet Gateway process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping


REST_ORIGIN = "https://testnet.binance.vision"
WS_URL = "wss://ws-api.testnet.binance.vision/ws-api/v3"
HASH_PATTERN = re.compile(r"[a-f0-9]{64}")
RAW_SECRET_NAMES = frozenset(
    {
        "BINANCE_API_KEY",
        "BINANCE_SECRET_KEY",
        "SPOT_TESTNET_API_KEY",
        "SPOT_TESTNET_SECRET_KEY",
    }
)


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    deployment_enabled: bool
    environment: str | None
    rest_origin: str | None
    websocket_url: str | None
    api_key_file: Path | None
    signing_secret_file: Path | None
    allowlist_digest: str | None
    gateway_instance_id: str | None
    build_digest: str | None
    configuration_digest: str | None

    @property
    def can_attempt_network(self) -> bool:
        """Settings alone can never authorize an exchange network call."""
        return False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "GatewaySettings":
        if values.get("TRADING_MODE") != "paper":
            raise ValueError("TRADING_MODE must be explicitly paper")
        if any(values.get(name) for name in RAW_SECRET_NAMES):
            raise ValueError("RAW_SECRET_ENV_FORBIDDEN")
        raw_enabled = values.get("SPOT_TESTNET_GATEWAY_ENABLED", "false")
        if raw_enabled not in {"true", "false"}:
            raise ValueError("SPOT_TESTNET_GATEWAY_ENABLED must be true or false")
        enabled = raw_enabled == "true"
        if not enabled:
            return cls(False, None, None, None, None, None, None, None, None, None)

        environment = values.get("SPOT_TESTNET_ENVIRONMENT")
        rest_origin = values.get("SPOT_TESTNET_REST_ORIGIN")
        websocket_url = values.get("SPOT_TESTNET_WS_URL")
        allowlist_digest = values.get("SPOT_TESTNET_ALLOWLIST_DIGEST")
        gateway_instance_id = values.get("SPOT_TESTNET_GATEWAY_INSTANCE_ID")
        build_digest = values.get("SPOT_TESTNET_GATEWAY_BUILD_DIGEST")
        configuration_digest = values.get("SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST")
        if environment != "BINANCE_SPOT_TESTNET":
            raise ValueError("SPOT_TESTNET_ENVIRONMENT_MISMATCH")
        if rest_origin != REST_ORIGIN or websocket_url != WS_URL:
            raise ValueError("SPOT_TESTNET_ENDPOINT_MISMATCH")
        for name, value in (
            ("SPOT_TESTNET_ALLOWLIST_DIGEST", allowlist_digest),
            ("SPOT_TESTNET_GATEWAY_INSTANCE_ID", gateway_instance_id),
            ("SPOT_TESTNET_GATEWAY_BUILD_DIGEST", build_digest),
            ("SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST", configuration_digest),
        ):
            if value is None or HASH_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{name}_INVALID")
        api_key_file = _absolute_file_path(values.get("SPOT_TESTNET_API_KEY_FILE"))
        signing_secret_file = _absolute_file_path(values.get("SPOT_TESTNET_SIGNING_SECRET_FILE"))
        if api_key_file == signing_secret_file:
            raise ValueError("SPOT_TESTNET_SECRET_PATHS_MUST_DIFFER")
        return cls(
            True,
            environment,
            rest_origin,
            websocket_url,
            api_key_file,
            signing_secret_file,
            allowlist_digest,
            gateway_instance_id,
            build_digest,
            configuration_digest,
        )


def _absolute_file_path(raw: str | None) -> Path:
    if raw is None or not raw:
        raise ValueError("SPOT_TESTNET_SECRET_FILE_PATH_REQUIRED")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("SPOT_TESTNET_SECRET_FILE_PATH_MUST_BE_ABSOLUTE")
    return path.resolve()
