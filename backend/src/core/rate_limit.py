import time
from collections import defaultdict, deque

from fastapi import HTTPException, status
from redis import Redis
from redis.exceptions import RedisError

from src.core.config import settings


class InMemoryRateLimiter:
    def __init__(self, max_requests: int = 120, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        now = time.time()
        bucket = self._buckets[key]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        bucket.append(now)


class RedisRateLimiter:
    def __init__(
        self, redis_client: Redis, max_requests: int = 120, window_seconds: int = 60
    ) -> None:
        self.redis_client = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def hit(self, key: str) -> None:
        bucket_key = f"rate-limit:{key}:{int(time.time() // self.window_seconds)}"
        try:
            current = self.redis_client.incr(bucket_key)
            if current == 1:
                self.redis_client.expire(bucket_key, self.window_seconds)
        except RedisError:
            raise
        if current > self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )


_memory_fallback = InMemoryRateLimiter()
_redis_client: Redis | None = None
try:
    _redis_client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
except RedisError:
    _redis_client = None


def hit_rate_limit(key: str) -> None:
    if _redis_client is None:
        _memory_fallback.hit(key)
        return
    try:
        RedisRateLimiter(_redis_client).hit(key)
    except RedisError:
        _memory_fallback.hit(key)


_email_memory_fallback = InMemoryRateLimiter(max_requests=3, window_seconds=900)


def hit_email_send_rate_limit(email: str) -> None:
    """Stricter, per-recipient limit for endpoints that email an arbitrary, unauthenticated
    address (OTP codes, password reset) - the blanket per-IP limit alone doesn't stop someone
    from email-bombing a victim's inbox from a single IP or a handful of IPs."""
    if settings.debug:
        return
    key = f"email-send:{email.strip().lower()}"
    if _redis_client is None:
        _email_memory_fallback.hit(key)
        return
    try:
        RedisRateLimiter(_redis_client, max_requests=3, window_seconds=900).hit(key)
    except RedisError:
        _email_memory_fallback.hit(key)
