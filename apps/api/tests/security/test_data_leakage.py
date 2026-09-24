"""Data leakage controls (``security.md`` sections 7 and 9).

Three leak surfaces are asserted here without a database:

* ``/metrics`` is unauthenticated, so it must carry no tenant or user identifier;
* an error response must never contain a stack trace, a SQL fragment or a
  provider message; and
* a deactivated user cannot authenticate even with the correct password.

Cross-tenant retrieval and the cross-tenant conversation 404 are exercised
against a real database in ``tests/integration/test_security_matrix.py`` and
``tests/integration/test_tenant_isolation.py``.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from coursellm.api.app import create_app
from coursellm.core.config import Settings
from coursellm.core.errors import AuthenticationError
from coursellm.repositories.identity import AuthLookup, AuthLookupResult
from coursellm.security.passwords import hash_password
from coursellm.services import auth as auth_service

pytestmark = pytest.mark.security

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="local",
        debug=True,
        secret_key="test-" + "not-a-real-key-" * 5,
        llm_enabled=False,
        embedding_provider="hashing",
        rerank_enabled=False,
        cache_enabled=False,
        database_url="postgresql+asyncpg://localhost:5432/coursellm_test",
    )


class TestMetricsCarriesNoTenantIdentifier:
    async def test_the_metrics_body_has_no_tenant_or_user_label(self) -> None:
        app = create_app(_settings())
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/metrics")
        assert response.status_code == 200
        body = response.text
        lowered = body.lower()
        assert "tenant_id" not in lowered
        assert "user_id" not in lowered
        assert not _UUID_RE.search(body), "a tenant or user identifier reached /metrics"


class TestErrorResponsesDoNotLeakInternals:
    async def test_a_provider_or_sql_failure_returns_only_the_envelope(self) -> None:
        app: FastAPI = create_app(_settings())
        secret_internals = (
            "SELECT * FROM users WHERE tenant_id = 'abc'",
            "openai.AuthenticationError: invalid api key",
        )

        @app.get("/boom")
        async def _boom() -> None:
            raise RuntimeError(" | ".join(secret_internals))

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/boom")
        assert response.status_code == 500
        raw = response.text
        for fragment in secret_internals:
            assert fragment not in raw
        assert "Traceback" not in raw
        assert "RuntimeError" not in raw
        body = response.json()
        assert body["error"] == "internal_error"
        assert body["request_id"]


class TestDeactivatedUser:
    async def test_a_deactivated_account_cannot_authenticate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        password = "correct-horse-battery-staple"
        lookup = AuthLookupResult(
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            role="owner",  # type: ignore[arg-type]
            hashed_password=hash_password(password),
            is_active=False,
            tenant_is_active=True,
        )

        async def fake_find(self: AuthLookup, _email: str) -> AuthLookupResult:
            return lookup

        monkeypatch.setattr(AuthLookup, "find_for_login", fake_find)

        class FakeSession:
            async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("a deactivated account must not reach the database")

        with pytest.raises(AuthenticationError, match="deactivated"):
            await auth_service.authenticate(
                FakeSession(),  # type: ignore[arg-type]
                _settings(),
                email="former@example.com",
                password=password,
            )

    async def test_a_wrong_password_is_indistinguishable_from_no_account(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_find(self: AuthLookup, _email: str) -> None:
            return None

        monkeypatch.setattr(AuthLookup, "find_for_login", fake_find)

        class FakeSession:
            async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("no query should run for a missing account")

        with pytest.raises(AuthenticationError, match="Incorrect email or password"):
            await auth_service.authenticate(
                FakeSession(),  # type: ignore[arg-type]
                _settings(),
                email="nobody@example.com",
                password="whatever",
            )
