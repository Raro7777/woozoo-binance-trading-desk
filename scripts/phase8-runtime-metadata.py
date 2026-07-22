"""Emit non-secret Phase 8 runtime identity used by local startup."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/python/platform-core/src"))
sys.path.insert(0, str(ROOT / "services/control-api/src"))

from control_api.testnet_operator import ALLOWLIST_DIGEST, CONFIGURATION_DIGEST  # noqa: E402


def _build_digest() -> str:
    digest = sha256()
    roots = (
        ROOT / "services/spot-testnet-gateway/src",
        ROOT / "services/testnet-execution-service/src",
    )
    files = [
        ROOT / "compose.phase8.yaml",
        ROOT / "infra/containers/phase8-spot-testnet-gateway.Dockerfile",
        ROOT / "infra/containers/phase8-testnet-execution.Dockerfile",
        ROOT / "infra/runtime/phase8-entrypoint.py",
    ]
    for source_root in roots:
        files.extend(path for path in source_root.rglob("*.py") if "__pycache__" not in path.parts)
    for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def main() -> int:
    print(
        json.dumps(
            {
                "allowlist_digest": ALLOWLIST_DIGEST,
                "configuration_digest": CONFIGURATION_DIGEST,
                "build_digest": _build_digest(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
