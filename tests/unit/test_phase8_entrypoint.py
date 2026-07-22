from __future__ import annotations

import importlib.util
from pathlib import Path
import socket

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "infra" / "runtime" / "phase8-entrypoint.py"
SPEC = importlib.util.spec_from_file_location("phase8_entrypoint", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
phase8_entrypoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phase8_entrypoint)


def address(ip: str) -> tuple[int, int, int, str, tuple[str, int]]:
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))


def test_control_api_trusts_one_resolved_private_proxy_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHASE8_CONTROL_API_TRUSTED_PROXY_HOST", "control-api-loopback-proxy")
    monkeypatch.setattr(
        phase8_entrypoint.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [address("172.31.0.3"), address("172.31.0.3")],
    )

    assert phase8_entrypoint._trusted_control_proxy_ip() == "172.31.0.3"


@pytest.mark.parametrize(
    "resolved",
    [
        [],
        [address("172.31.0.3"), address("172.31.0.4")],
        [address("8.8.8.8")],
    ],
)
def test_control_api_rejects_missing_ambiguous_or_public_proxy_address(
    monkeypatch: pytest.MonkeyPatch,
    resolved: list[tuple[int, int, int, str, tuple[str, int]]],
) -> None:
    monkeypatch.setenv("PHASE8_CONTROL_API_TRUSTED_PROXY_HOST", "control-api-loopback-proxy")
    monkeypatch.setattr(
        phase8_entrypoint.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: resolved,
    )

    with pytest.raises(SystemExit, match="trusted proxy"):
        phase8_entrypoint._trusted_control_proxy_ip()
