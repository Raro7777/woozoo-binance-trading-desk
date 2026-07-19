"""Health probes for the platform's durable and non-authoritative dependencies."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
import redis

from platform_core.config import PlatformSettings


@dataclass(frozen=True, slots=True)
class DependencySnapshot:
    postgres: str
    redis: str


class HealthProbe:
    def snapshot(self, settings: PlatformSettings) -> DependencySnapshot:
        raise NotImplementedError


class PlatformHealthProbe(HealthProbe):
    def snapshot(self, settings: PlatformSettings) -> DependencySnapshot:
        database_url = settings.require_service_dependencies().database_url
        redis_url = settings.require_service_dependencies().redis_url
        assert database_url is not None
        assert redis_url is not None

        try:
            with psycopg.connect(database_url, connect_timeout=1) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
        except psycopg.Error:
            return DependencySnapshot(postgres="unavailable", redis="unknown")

        try:
            client = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
            client.ping()
        except redis.RedisError:
            return DependencySnapshot(postgres="healthy", redis="unavailable")

        return DependencySnapshot(postgres="healthy", redis="healthy")


class StaticHealthProbe(HealthProbe):
    def __init__(self, snapshot: DependencySnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self, settings: PlatformSettings) -> DependencySnapshot:
        return self._snapshot
