from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


def mypy(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(path)],
        cwd=ROOT,
        env={
            **os.environ,
            "MYPYPATH": str(ROOT / "packages" / "python" / "platform-core" / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_python_market_payload_binding_accepts_only_the_closed_shape() -> None:
    valid = mypy(FIXTURES / "python_market_binding_valid.py")
    invalid = mypy(FIXTURES / "python_market_binding_invalid.py")

    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert invalid.returncode != 0
    assert 'Missing key "quantity" for TypedDict "TradePayloadBindingV1"' in invalid.stdout
