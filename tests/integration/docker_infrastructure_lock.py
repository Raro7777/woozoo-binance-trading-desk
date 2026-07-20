"""One cross-process lock for tests that mutate the shared Docker Compose project."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import time
from typing import Iterator


LOCK_PATH = Path(tempfile.gettempdir()) / "woozoo-docker-integration.lock"


@contextmanager
def docker_infrastructure_lock(*, timeout_seconds: float = 300) -> Iterator[None]:
    """Serialize destructive Compose fixtures on byte zero across processes."""
    with LOCK_PATH.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("could not acquire shared Docker infrastructure lock")
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
