"""Read Gateway-only secret mounts without caching them in shared configuration."""

from __future__ import annotations

import os
from pathlib import Path
import stat


def read_secret_file(path: Path) -> bytearray:
    if not path.is_absolute() or not path.is_file():
        raise ValueError("GATEWAY_SECRET_FILE_UNAVAILABLE")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("GATEWAY_SECRET_FILE_PERMISSIONS_INVALID")
    raw = bytearray(path.read_bytes())
    while raw.endswith((b"\r", b"\n")):
        raw.pop()
    if not raw or len(raw) > 4096 or any(value < 0x21 or value > 0x7E for value in raw):
        _zero(raw)
        raise ValueError("GATEWAY_SECRET_FILE_INVALID")
    return raw


def _zero(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0
