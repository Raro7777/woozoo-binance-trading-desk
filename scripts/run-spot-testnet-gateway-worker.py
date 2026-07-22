"""Repository launcher for the isolated Spot Testnet Gateway package."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "spot-testnet-gateway" / "src"))

from spot_testnet_gateway.worker import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
