"""Rate limiting (``security.md`` section 11): enforcement, reset and fail-open.

The limiter is exercised both directly and through the API. The API tests assert
the two contract details a client depends on: a ``429`` carries ``Retry-After``,
and a Redis outage does **not** turn into a ``500`` — the request proceeds and the
bypass is logged.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from coursellm.api.app import create_app
from coursellm.api.deps import get_rate_limiter_dep, get_settings_dep
from coursellm.core.config import Settings
from coursellm.security import ratelimit
from coursellm.security.ratelimit import (
    InMemoryRateLimiter,
    RedisRateLimiter,
)

pytestmark = pytest.mark.security


class _Clock:
    """A controllable monotonic clock so a fixed window can be advanced."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))


class _BrokenRedis:
    """A client whose every command fails, standing in for an outage."""

    async def eval(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("redis is down")

    async def aclose(self) -> None:
        return None


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(self, _script: str, _numkeys: int, key: str, _window: int) -> list[int]:
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], 60]


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "rate_limit_requests_per_minute": 2,
        "rate_limit_llm_requests_per_hour": 200,
        "cache_enabled": False,
        "llm_enabled": False,
        "embedding_provider": "hashing",
        "rerank_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class TestInMemoryLimiter:
    async def test_the_n_plus_first_request_in_a_window_is_denied(self) -> None:
        limiter = InMemoryRateLimiter()
        assert (await limiter.check("k", limit=2, window_seconds=60)).allowed is True
        assert (await limiter.check("k", limit=2, window_seconds=60)).allowed is True
        denied = await limiter.check("k", limit=2, window_seconds=60)
        assert denied.allowed is False
        assert denied.remaining == 0
        assert denied.retry_after >= 1

    async def test_the_window_resets_after_its_duration(self) -> None:
        clock = _Clock()
        limiter = InMemoryRateLimiter(clock=clock)
        assert (await limiter.check("k", limit=1, window_seconds=10)).allowed is True
        assert (await limiter.check("k", limit=1, window_seconds=10)).allowed is False
        clock.now = 10.0
        assert (await limiter.check("k", limit=1, window_seconds=10)).allowed is True

    async def test_distinct_keys_have_distinct_windows(self) -> None:
        limiter = InMemoryRateLimiter()
        assert (await limiter.check("a", limit=1, window_seconds=60)).allowed is True
        assert (await limiter.check("b", limit=1, window_seconds=60)).allowed is True


class TestRedisLimiter:
    async def test_an_atomic_script_result_is_honoured(self) -> None:
        limiter = RedisRateLimiter("redis://unused", client=_FakeRedis())  # type: ignore[arg-type]
        assert (await limiter.check("k", limit=2, window_seconds=60)).allowed is True
        assert (await limiter.check("k", limit=2, window_seconds=60)).allowed is True
        denied = await limiter.check("k", limit=2, window_seconds=60)
        assert denied.allowed is False
        assert denied.retry_after == 60

    async def test_a_redis_outage_falls_back_and_logs_a_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _RecordingLogger()
        monkeypatch.setattr(ratelimit, "logger", recorder)
        limiter = RedisRateLimiter("redis://unused", client=_BrokenRedis())  # type: ignore[arg-type]
        decision = await limiter.check("k", limit=5, window_seconds=60)
        assert decision.allowed is True
        assert recorder.warnings, "the bypass must be visible in the logs"
        assert recorder.warnings[0][0] == "rate_limit_redis_unavailable"


async def _make_client(app_settings: Settings, limiter: Any) -> AsyncClient:
    app = create_app(app_settings)
    app.dependency_overrides[get_settings_dep] = lambda: app_settings
    app.dependency_overrides[get_rate_limiter_dep] = lambda: limiter
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


class TestApiEnforcement:
    async def test_the_n_plus_first_request_returns_429_with_retry_after(self) -> None:
        settings = _settings(rate_limit_requests_per_minute=2)
        client = await _make_client(settings, InMemoryRateLimiter())
        async with client:
            # No credentials, so the route itself answers 401; the limiter has
            # already counted the request and that is what is under test.
            for _ in range(2):
                response = await client.post("/api/v1/chat", json={"question": "hi"})
                assert response.status_code != 429
            limited = await client.post("/api/v1/chat", json={"question": "hi"})
        assert limited.status_code == 429
        assert limited.headers.get("Retry-After")
        body = limited.json()
        assert body["error"] == "rate_limited"
        assert body["detail"]
        assert body["request_id"]

    async def test_the_login_endpoint_is_limited(self) -> None:
        settings = _settings(rate_limit_requests_per_minute=1)
        client = await _make_client(settings, InMemoryRateLimiter())
        async with client:
            first = await client.post(
                "/api/v1/auth/token", json={"email": "a@example.com", "password": "x"}
            )
            assert first.status_code != 429
            limited = await client.post(
                "/api/v1/auth/token", json={"email": "a@example.com", "password": "x"}
            )
        assert limited.status_code == 429
        assert limited.headers.get("Retry-After")

    async def test_a_redis_outage_fails_open_at_the_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _RecordingLogger()
        monkeypatch.setattr(ratelimit, "logger", recorder)
        limiter = RedisRateLimiter("redis://unused", client=_BrokenRedis())  # type: ignore[arg-type]
        settings = _settings(rate_limit_requests_per_minute=5)
        client = await _make_client(settings, limiter)
        async with client:
            first = await client.post("/api/v1/chat", json={"question": "hi"})
            second = await client.post("/api/v1/chat", json={"question": "hi"})
        assert first.status_code != 500
        assert second.status_code != 500
        assert second.status_code != 429, "a Redis outage must not refuse traffic"
        assert recorder.warnings, "the bypass must be logged"
