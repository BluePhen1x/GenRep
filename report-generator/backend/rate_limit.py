"""
Redis sliding window rate limiter with in-memory fallback.

Used to throttle the /generate endpoint per-user.
"""

import time
from typing import Optional

try:
    import redis as redis_sync
except ImportError:
    redis_sync = None

from config import config


class RedisRateLimiter:
    """Sliding window counter using Redis sorted sets."""

    def __init__(self, redis_url: str):
        self.pool = redis_sync.ConnectionPool.from_url(redis_url, decode_responses=True)
        self.client = redis_sync.Redis(connection_pool=self.pool)

    def is_allowed(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds

        pipe = self.client.pipeline()
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 10)
        results = pipe.execute()

        count = results[2]
        return count <= limit

    def get_remaining(self, key: str, limit: int, window_seconds: int) -> int:
        now = time.time()
        window_start = now - window_seconds
        self.client.zremrangebyscore(key, "-inf", window_start)
        count = self.client.zcard(key)
        return max(0, limit - count)

    def close(self):
        self.client.close()


class InMemoryRateLimiter:
    """Fallback when Redis is unavailable."""

    def __init__(self):
        self._store: dict[str, list[float]] = {}

    def is_allowed(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds

        if key not in self._store:
            self._store[key] = []

        self._store[key] = [t for t in self._store[key] if t > window_start]
        self._store[key].append(now)
        return len(self._store[key]) <= limit

    def get_remaining(self, key: str, limit: int, window_seconds: int) -> int:
        now = time.time()
        window_start = now - window_seconds
        timestamps = self._store.get(key, [])
        count = sum(1 for t in timestamps if t > window_start)
        return max(0, limit - count)

    def close(self):
        pass


_limiter = None


def get_rate_limiter():
    global _limiter
    if _limiter is not None:
        return _limiter

    try:
        limiter = RedisRateLimiter(config.REDIS_URL)
        limiter.client.ping()
        _limiter = limiter
    except Exception:  # noqa: BLE001
        _limiter = InMemoryRateLimiter()

    return _limiter


def parse_rate_limit(spec: str) -> tuple[int, int]:
    """Parse '5/minute' -> (5, 60)."""
    count, period = spec.split("/")
    count = int(count)
    multipliers = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }
    window = multipliers.get(period, 60)
    return count, window
