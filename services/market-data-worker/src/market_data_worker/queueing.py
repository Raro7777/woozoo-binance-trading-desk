"""Bounded ingress queue that reports overflow rather than dropping silently."""

from __future__ import annotations

from collections import deque
from typing import Generic, TypeVar

from .types import QualityStatus


T = TypeVar("T")


class BackpressureOverflow(RuntimeError, Generic[T]):
    def __init__(self, payload: T) -> None:
        super().__init__("bounded market-data ingress queue is full")
        self.payload = payload


class BoundedIngressQueue(Generic[T]):
    def __init__(self, capacity: int = 10_000) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: deque[T] = deque()
        self.status = QualityStatus.HEALTHY
        self.overflow_count = 0

    @property
    def size(self) -> int:
        return len(self._items)

    def offer(self, payload: T) -> None:
        if len(self._items) >= self.capacity:
            self.status = QualityStatus.INVALID
            self.overflow_count += 1
            raise BackpressureOverflow(payload)
        self._items.append(payload)

    def take(self) -> T:
        if not self._items:
            raise IndexError("ingress queue is empty")
        return self._items.popleft()
