from __future__ import annotations

import multiprocessing

from docker_infrastructure_lock import docker_infrastructure_lock


def _hold_lock(
    ready: multiprocessing.synchronize.Event, release: multiprocessing.synchronize.Event
) -> None:
    with docker_infrastructure_lock(timeout_seconds=10):
        ready.set()
        release.wait(10)


def _probe_locked(result: multiprocessing.queues.Queue) -> None:
    try:
        with docker_infrastructure_lock(timeout_seconds=0.5):
            result.put("acquired")
    except TimeoutError:
        result.put("timed-out")


def _enter_lock(acquired: multiprocessing.synchronize.Event) -> None:
    with docker_infrastructure_lock(timeout_seconds=10):
        acquired.set()


def test_shared_docker_lock_serializes_two_spawned_processes() -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    probe_result = context.Queue()
    acquired = context.Event()
    holder = context.Process(target=_hold_lock, args=(ready, release))
    probe = context.Process(target=_probe_locked, args=(probe_result,))
    contender = context.Process(target=_enter_lock, args=(acquired,))
    holder.start()
    try:
        assert ready.wait(10)
        probe.start()
        assert probe_result.get(timeout=10) == "timed-out"
        probe.join(10)
        assert probe.exitcode == 0
        assert holder.is_alive()
        release.set()
        holder.join(10)
        assert holder.exitcode == 0
        contender.start()
        assert acquired.wait(10)
    finally:
        release.set()
        holder.join(10)
        if probe.pid is not None:
            probe.join(10)
        if contender.pid is not None:
            contender.join(10)
    assert contender.exitcode == 0
