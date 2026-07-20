from __future__ import annotations

import multiprocessing

from docker_infrastructure_lock import docker_infrastructure_lock


def _hold_lock(
    ready: multiprocessing.synchronize.Event, release: multiprocessing.synchronize.Event
) -> None:
    with docker_infrastructure_lock(timeout_seconds=10):
        ready.set()
        release.wait(10)


def _enter_lock(acquired: multiprocessing.synchronize.Event) -> None:
    with docker_infrastructure_lock(timeout_seconds=10):
        acquired.set()


def test_shared_docker_lock_serializes_two_spawned_processes() -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    acquired = context.Event()
    holder = context.Process(target=_hold_lock, args=(ready, release))
    contender = context.Process(target=_enter_lock, args=(acquired,))
    holder.start()
    try:
        assert ready.wait(10)
        contender.start()
        assert not acquired.wait(0.5)
        release.set()
        assert acquired.wait(10)
    finally:
        release.set()
        holder.join(10)
        if contender.pid is not None:
            contender.join(10)
    assert holder.exitcode == 0
    assert contender.exitcode == 0
