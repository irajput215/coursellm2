"""Integration-test fixtures backed by a real PostgreSQL instance.

This package is the only place where Row-Level Security is exercised. That makes
the *role* under test load-bearing: PostgreSQL ignores every policy for a
superuser or a role holding ``BYPASSRLS``, silently and without warning. An
isolation test that connects as the schema owner therefore proves nothing, so
the fixtures keep two engines deliberately separate:

* ``pg_settings`` drives the application engine, which connects as
  ``coursellm_app`` (``NOSUPERUSER NOBYPASSRLS``) and is subject to every policy.
* ``owner_engine`` connects as the schema owner and is used only for direct-SQL
  setup and assertions that must see all rows regardless of RLS.

Every async fixture pins ``loop_scope="function"``. The project configures
``asyncio_default_fixture_loop_scope = "session"`` while tests default to a
function-scoped loop; leaving the default here would create the engine on one
event loop and await it on another, which fails with "attached to a different
loop". Pinning both to the same scope keeps the engine and its pool on the loop
that uses them.
"""

from __future__ import annotations

import getpass
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from coursellm.api.app import create_app
from coursellm.api.deps import get_settings_dep
from coursellm.core.config import Environment, Settings
from coursellm.db.base import GLOBAL_TABLES, TENANT_SCOPED_TABLES
from coursellm.db.session import dispose_engine
from coursellm.security.passwords import hash_password

# The literal connection strings from the local bootstrap. ``TEST_DATABASE_URL``
# is honoured so CI can point the suite at a different host without editing
# tests; the owner URL has its own override for the same reason.
DEFAULT_APP_DATABASE_URL = "postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_test"
OWNER_DATABASE_URL_ENV = "TEST_OWNER_DATABASE_URL"

SEED_PASSWORD = "seed-password-123"

# ``tenants`` and the global catalogue (``resources``/``resource_concepts``) are
# outside the RLS boundary but still have to be emptied between tests, so they
# come from ``GLOBAL_TABLES`` rather than being repeated in each test module.
# ``alembic_version`` is the one exception: truncating it would make the database
# look unmigrated. The tenant-scoped tables come from the single source of truth
# in ``coursellm.db.base`` so a new one cannot be forgotten here.
_TRUNCATABLE_GLOBAL: frozenset[str] = frozenset(GLOBAL_TABLES) - {"alembic_version"}
_TABLES_TO_TRUNCATE: tuple[str, ...] = tuple(sorted(_TRUNCATABLE_GLOBAL | TENANT_SCOPED_TABLES))


def _owner_database_url() -> str:
    explicit = os.environ.get(OWNER_DATABASE_URL_ENV)
    if explicit:
        return explicit
    return f"postgresql+asyncpg://{getpass.getuser()}@localhost:5432/coursellm_test"


@pytest.fixture
def pg_settings() -> Settings:
    """Settings that point the application at the RLS-enforcing app role.

    ``db_pool_size=1`` with no overflow is deliberate: it makes the
    pooled-connection hazard test deterministic by guaranteeing that a second
    session checks out the same physical connection the first one used.
    """
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=Environment.LOCAL,
        debug=True,
        secret_key="integration-" + "not-a-real-signing-key-" * 3,
        llm_enabled=False,
        embedding_provider="hashing",
        rerank_enabled=False,
        cache_enabled=False,
        otel_enabled=False,
        langsmith_enabled=False,
        database_url=os.environ.get("TEST_DATABASE_URL", DEFAULT_APP_DATABASE_URL),
        db_pool_size=1,
        db_max_overflow=0,
    )


@pytest_asyncio.fixture(loop_scope="function")
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    """An engine connected as the schema owner, for RLS-independent assertions.

    This role is expected to be a superuser locally, which is exactly why it must
    never be the role used to prove that isolation works.
    """
    engine = create_async_engine(_owner_database_url())
    try:
        yield engine
    finally:
        await engine.dispose()


async def _truncate_all(engine: AsyncEngine) -> None:
    statement = "TRUNCATE TABLE " + ", ".join(_TABLES_TO_TRUNCATE) + " RESTART IDENTITY CASCADE"
    async with engine.begin() as connection:
        await connection.execute(text(statement))


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def clean_db(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    """Empty every table before each test so tests cannot depend on each other.

    ``RESTART IDENTITY CASCADE`` resets sequences and follows foreign keys, so a
    partially written graph from a failed test cannot survive into the next one.
    The owner engine is used because the app role is (correctly) unable to
    delete rows it cannot see.
    """
    await _truncate_all(owner_engine)
    yield
    await _truncate_all(owner_engine)


@dataclass(frozen=True, slots=True)
class SeededTenant:
    """The identifiers created for one seeded tenant."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    password: str
    course_id: uuid.UUID
    document_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Seeded:
    """Two independent tenants, each with a user, a course and a document."""

    tenant_a: SeededTenant
    tenant_b: SeededTenant

    @property
    def tenant_a_id(self) -> uuid.UUID:
        return self.tenant_a.tenant_id

    @property
    def tenant_b_id(self) -> uuid.UUID:
        return self.tenant_b.tenant_id

    @property
    def user_a_id(self) -> uuid.UUID:
        return self.tenant_a.user_id

    @property
    def user_b_id(self) -> uuid.UUID:
        return self.tenant_b.user_id

    @property
    def course_a_id(self) -> uuid.UUID:
        return self.tenant_a.course_id

    @property
    def course_b_id(self) -> uuid.UUID:
        return self.tenant_b.course_id


async def _insert_tenant_graph(
    engine: AsyncEngine, *, label: str, name: str, slug: str, email: str
) -> SeededTenant:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    course_id = uuid.uuid4()
    document_id = uuid.uuid4()
    hashed_password = hash_password(SEED_PASSWORD)

    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": str(tenant_id), "name": name, "slug": slug},
        )
        await connection.execute(
            text(
                "INSERT INTO users "
                "(id, tenant_id, email, hashed_password, full_name, role, is_active) "
                "VALUES (:id, :tenant_id, :email, :hashed, :full_name, 'owner', true)"
            ),
            {
                "id": str(user_id),
                "tenant_id": str(tenant_id),
                "email": email,
                "hashed": hashed_password,
                "full_name": f"{label} Owner",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO courses (id, tenant_id, user_id, name, code) "
                "VALUES (:id, :tenant_id, :user_id, :name, :code)"
            ),
            {
                "id": str(course_id),
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "name": f"{label} Course",
                "code": label.upper(),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO documents "
                "(id, tenant_id, course_id, user_id, filename, storage_key, "
                " content_type, size_bytes, sha256) "
                "VALUES (:id, :tenant_id, :course_id, :user_id, :filename, :storage_key, "
                " 'text/plain', 128, :sha256)"
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "course_id": str(course_id),
                "user_id": str(user_id),
                "filename": f"{label.lower()}-notes.txt",
                "storage_key": f"{slug}/{uuid.uuid4().hex}.txt",
                "sha256": uuid.uuid4().hex + uuid.uuid4().hex,
            },
        )

    return SeededTenant(
        tenant_id=tenant_id,
        user_id=user_id,
        email=email,
        password=SEED_PASSWORD,
        course_id=course_id,
        document_id=document_id,
    )


@pytest_asyncio.fixture(loop_scope="function")
async def seeded(owner_engine: AsyncEngine) -> Seeded:
    """Two tenants with one user, one course and one document each.

    Written through the owner engine so that Row-Level Security cannot hide the
    rows from the fixture that is supposed to create them. The application role
    is the one under test, and it must not be able to do this for another tenant.
    """
    tenant_a = await _insert_tenant_graph(
        owner_engine,
        label="Alpha",
        name="Alpha University",
        slug="alpha",
        email="alpha@example.com",
    )
    tenant_b = await _insert_tenant_graph(
        owner_engine, label="Beta", name="Beta University", slug="beta", email="beta@example.com"
    )
    return Seeded(tenant_a=tenant_a, tenant_b=tenant_b)


@pytest_asyncio.fixture(loop_scope="function")
async def api_client(pg_settings: Settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to an app built with the RLS-enforcing settings.

    ``create_app`` receives ``pg_settings`` directly, but the request handlers
    resolve settings through ``get_settings_dep``, which reads the module-level
    cached accessor. The dependency override is what actually makes the app use
    the test database as the app role; without it every request would quietly
    fall back to the cached development settings.
    """
    application = create_app(pg_settings)
    application.dependency_overrides[get_settings_dep] = lambda: pg_settings
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        application.dependency_overrides.clear()


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def dispose() -> AsyncIterator[None]:
    """Release the process-wide engine so connections do not leak between tests.

    The engine is a module-level singleton keyed to whichever settings created
    it first. Disposing it after each test also guarantees that a test which
    never set the tenancy variable starts on a physical connection that never
    had it set, so "fails closed" is a property of the system rather than of
    test ordering.
    """
    yield
    await dispose_engine()
