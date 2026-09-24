"""Fixed-window rate limiting with a Redis backend and an in-process fallback.

Two limits are enforced by the API (``security.md`` section 11): a per-user
request ceiling per minute on every route, and a per-user ceiling per hour on the
LLM-backed routes. Both are the same primitive with different windows.

**Correctness across processes.** :class:`RedisRateLimiter` increments and expires
in a single Lua script, so two workers cannot both read ``limit - 1`` and both
allow the request that crosses the limit. A read-modify-write across two round
trips would be correct in a single process and wrong in production, which is the
worst combination because tests pass.

**Failure semantics.** Rate limiting fails *open* when Redis is unreachable: the
request proceeds and the bypass is logged. Refusing all traffic because a cache is
down would trade a cost risk for an availability outage. The in-process
:class:`InMemoryRateLimiter` is used both by tests and as the degraded backend, so
a Redis outage still applies a per-process ceiling rather than none at all — but
it never turns into a 500.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict

from coursellm.core.config import Settings
from coursellm.core.errors import RateLimitError
from coursellm.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from redis.asyncio import Redis

logger = get_logger(__name__)

#: Atomic fixed-window increment. ``EXPIRE`` is only set on the first hit, so a
#: steady stream of requests cannot keep pushing the window's expiry forward.
_INCR_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""


class RateLimitDecision(BaseModel):
    """The outcome of one check, including what the response should advertise."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    limit: int
    remaining: int
    retry_after: int = 0


class RateLimiter(Protocol):
    """The limiter contract. Implementations are interchangeable."""

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """Consume one unit for ``key`` and report whether it is allowed."""
        ...


class RateLimitExceededError(RateLimitError):
    """A 429 that carries the ``Retry-After`` value the client should honour."""

    def __init__(self, detail: str, *, retry_after: int) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


class InMemoryRateLimiter:
    """A fixed-window limiter for one process.

    Used by tests and as the degraded backend when Redis is unreachable. Not
    shared between workers, so it is not correct as the primary limiter in a
    multi-process deployment; it is correct as a bounded fallback.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._windows: dict[str, tuple[float, int]] = {}

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        now = self._clock()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)
        if count > limit:
            elapsed = now - start
            retry_after = max(int(window_seconds - elapsed) + 1, 1)
            return RateLimitDecision(
                allowed=False, limit=limit, remaining=0, retry_after=retry_after
            )
        return RateLimitDecision(allowed=True, limit=limit, remaining=limit - count)

    def reset(self) -> None:
        """Drop every window. Used by tests and by an operator-triggered clear."""
        self._windows.clear()


class RedisRateLimiter:
    """A fixed-window limiter backed by an atomic Redis script.

    The client is created lazily so constructing the limiter opens no connection.
    Any Redis failure is logged and delegated to the in-process fallback: the
    request proceeds rather than becoming a 500.
    """

    def __init__(
        self,
        url: str,
        *,
        fallback: RateLimiter | None = None,
        client: Redis | None = None,
    ) -> None:
        self._url = url
        self._fallback: RateLimiter = fallback or InMemoryRateLimiter()
        self._client = client

    async def _redis(self) -> Redis:
        if self._client is None:
            from redis.asyncio import from_url

            self._client = from_url(self._url, decode_responses=True)
        return self._client

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        try:
            client = await self._redis()
            raw: Any = await client.eval(_INCR_SCRIPT, 1, key, window_seconds)
            current, ttl = int(raw[0]), int(raw[1])
        except Exception as exc:  # availability beats strictness for a cache
            logger.warning(
                "rate_limit_redis_unavailable",
                error_type=type(exc).__name__,
                impact="Rate limiting degraded to the in-process fallback.",
            )
            return await self._fallback.check(key, limit=limit, window_seconds=window_seconds)
        if current > limit:
            retry_after = max(ttl, 1)
            return RateLimitDecision(
                allowed=False, limit=limit, remaining=0, retry_after=retry_after
            )
        return RateLimitDecision(allowed=True, limit=limit, remaining=limit - current)

    async def close(self) -> None:
        """Release the Redis connection pool, if one was opened."""
        client = self._client
        self._client = None
        if client is not None:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                await close()


_limiter: RateLimiter | None = None


def get_rate_limiter(settings: Settings) -> RateLimiter:
    """Return the limiter for ``settings``.

    With the cache enabled (the production default) a process-wide
    :class:`RedisRateLimiter` is created once and shared. With the cache disabled
    a fresh :class:`InMemoryRateLimiter` is returned per call: an in-process
    counter that is never shared cannot enforce a ceiling across requests, so
    pretending otherwise would make tests that are not about rate limiting flaky
    while proving nothing. Tests that exercise the limiter install a shared
    instance with :func:`set_rate_limiter` or a FastAPI dependency override.
    """
    global _limiter
    if _limiter is not None:
        return _limiter
    if settings.cache_enabled:
        _limiter = RedisRateLimiter(settings.redis_url)
        return _limiter
    return InMemoryRateLimiter()


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    """Install a limiter process-wide. Used by tests and dependency overrides."""
    global _limiter
    _limiter = limiter


async def reset_rate_limiter() -> None:
    """Close and forget the process-wide limiter. Safe to call when unset."""
    global _limiter
    if isinstance(_limiter, RedisRateLimiter):
        await _limiter.close()
    _limiter = None


def user_key(context_key: str, *, scope: str) -> str:
    """Build a limiter key. Never includes a tenant or user id in a log line."""
    return f"ratelimit:{scope}:{context_key}"


__all__ = [
    "InMemoryRateLimiter",
    "RateLimitDecision",
    "RateLimitExceededError",
    "RateLimiter",
    "RedisRateLimiter",
    "get_rate_limiter",
    "reset_rate_limiter",
    "set_rate_limiter",
    "user_key",
]
