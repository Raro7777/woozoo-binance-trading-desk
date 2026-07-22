"""One-time local operator verifier bootstrap.

The plaintext source path is supplied explicitly and is never copied into the
repository, database, environment, stdout, logs, or generated verifier file.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from argon2 import PasswordHasher


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret-file", required=True, type=Path)
    parser.add_argument("--verifier-file", required=True, type=Path)
    args = parser.parse_args()
    source = args.secret_file.resolve(strict=True)
    target = args.verifier_file.resolve(strict=False)
    if source == target:
        raise SystemExit("secret and verifier paths must be different")
    password = source.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise SystemExit("local operator secret is empty")
    if target.exists():
        raise SystemExit("verifier file already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PasswordHasher().hash(password) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
