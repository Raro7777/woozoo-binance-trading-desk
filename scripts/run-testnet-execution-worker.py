"""Repository launcher for the deterministic Testnet execution package."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "testnet-execution-service" / "src"))

from testnet_execution.worker import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
