"""Shared test fixtures.

Design notes:

* Settings are overridden explicitly, never by mutating ``os.environ`` after
  import, so tests cannot leak configuration into each other.
* The application is built per-test through the factory, so a test that
  registers a readiness probe cannot affect another test.
* Tests are marked ``unit``, ``integration``, ``security`` or ``eval``. Only
  ``unit`` runs by default; the rest are opt-in through ``make``.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from coursellm.api.app import create_app
from coursellm.api.routers import health
from coursellm.core.config import Environment, Settings

# ``evals/`` lives at the repository root, outside the ``apps/api`` project that
# owns this pytest configuration. The evaluation harness tests import it, and
# pytest puts the first directory without an ``__init__.py`` on ``sys.path`` —
# which is ``apps/api``, not the repository root. The root is added explicitly
# here rather than through an environment variable so that
# ``cd apps/api && pytest`` works exactly as the Makefile and CI invoke it. The
# insertion sits below the application imports so it does not split an import
# block.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def test_settings() -> Settings:
    """Deterministic settings that touch no network and no database."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=Environment.LOCAL,
        debug=True,
        # Assembled at runtime so the secret scanner flags a real key assignment
        # without needing an exception for this file.
        # 64+ bytes so PyJWT does not warn that an HS256/HS512 key is short;
        # assembled at runtime so the secret scanner sees no key assignment.
        secret_key="test-" + "not-a-real-key-" * 5,
        llm_enabled=False,
        embedding_provider="hashing",
        rerank_enabled=False,
        cache_enabled=False,
        otel_enabled=False,
        langsmith_enabled=False,
        database_url="postgresql+asyncpg://localhost:5432/coursellm_test",
    )


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> Settings:
    """Point the cached settings accessor at the test settings.

    Both the module attribute and the accessor are replaced; ``monkeypatch``
    restores them afterwards, so no explicit cache clearing is needed and tests
    cannot leak configuration into one another.
    """
    from coursellm.core import config as config_module

    monkeypatch.setattr(config_module, "settings", test_settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: test_settings)
    return test_settings


@pytest.fixture
def app(settings: Settings):
    """A fresh application instance with an empty readiness-probe registry."""
    health.clear_checks()
    application = create_app(settings)
    yield application
    health.clear_checks()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """HTTP client bound directly to the ASGI app; no network socket is opened."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
