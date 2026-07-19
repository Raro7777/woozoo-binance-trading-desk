from __future__ import annotations

import pytest

from market_data_worker.queueing import BackpressureOverflow, BoundedIngressQueue
from market_data_worker.types import QualityStatus


def test_data_007_capacity_overflow_is_explicit_and_fail_closed() -> None:
    queue = BoundedIngressQueue(capacity=10_000)
    for sequence in range(10_000):
        queue.offer(("btcusdt@trade", sequence))

    with pytest.raises(BackpressureOverflow) as failure:
        queue.offer(("btcusdt@trade", 10_000))

    assert failure.value.payload == ("btcusdt@trade", 10_000)
    assert queue.status is QualityStatus.INVALID
    assert queue.size == 10_000
    assert queue.overflow_count == 1
