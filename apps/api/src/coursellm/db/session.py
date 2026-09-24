"""Async engine, session factory and request-scoped session dependencies.

The engine is created lazily and cached, not at import time. Importing a module
must not open connections: that is what made the previous implementation
untestable and made ``create_all()`` run as an import side effect.

Sessions handed to request handlers are always bound to a transaction with the
tenancy GUC already set. See :mod:`coursellm.db.tenancy` for why that ordering
matters.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from coursellm.core.config import Settings
from coursellm.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_lock = asyncio.Lock()


def _connect_args(settings: Settings) -> dict[str, Any]:
    """Driver-level settings.

    ``statement_timeout`` is enforced by the server, so it also covers queries
    issued by code that forgot a client-side timeout. ``application_name`` makes
    the source of a connection identifiable in ``pg_stat_activity``, which is the
    difference between diagnosing a connection-pool incident and guessing.
    """
    return {
        "server_settings": {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "application_name": f"{settings.app_name.lower()}-{settings.environment.value}",
        }
    }


def create_engine(settings: Settings) -> AsyncEngine:
    """Build a new engine. Callers should normally use :func:`get_engine`."""
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # survives a database restart without a stale-connection error
        pool_recycle=1800,  # below typical managed-Postgres idle timeouts
        connect_args=_connect_args(settings),
    )


async def get_engine(settings: Settings) -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine, _session_factory
    if _engine is None:
        async with _lock:
            if _engine is None:
                _engine = create_engine(settings)
                _session_factory = async_sessionmaker(
                    bind=_engine,
                    class_=AsyncSession,
                    expire_on_commit=False,  # objects stay usable after commit
                    autoflush=False,
                )
                logger.info("database_engine_created", pool_size=settings.db_pool_size)
    return _engine


async def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    await get_engine(settings)
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    """Close pooled connections. Called from the application shutdown path."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("database_engine_disposed")
    _engine = None
    _session_factory = None


async def check_connection(settings: Settings) -> None:
    """Readiness probe: prove the database is reachable and queryable."""
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def rls_enforcement_status(session: AsyncSession) -> dict[str, Any]:
    """Report whether Row-Level Security is actually enforced for this role.

    A superuser, or any role with ``BYPASSRLS``, ignores every RLS policy
    silently. That is the single most likely way for tenant isolation to be
    believed-but-absent, so it is queried explicitly at startup rather than
    assumed.
    """
    result = await session.execute(
        text(
            """
            SELECT current_user AS role,
                   r.rolsuper AS is_superuser,
                   r.rolbypassrls AS bypasses_rls
            FROM pg_roles r
            WHERE r.rolname = current_user
            """
        )
    )
    row = result.mappings().one()
    return {
        "role": row["role"],
        "is_superuser": row["is_superuser"],
        "bypasses_rls": row["bypasses_rls"],
        "enforced": not row["is_superuser"] and not row["bypasses_rls"],
    }


@asynccontextmanager
async def session_scope(settings: Settings) -> AsyncIterator[AsyncSession]:
    """Open a session in a transaction. For use outside the request cycle.

    Background workers and CLI commands use this. It deliberately does **not**
    set the tenancy GUC: callers that operate on tenant data must do so through
    :func:`coursellm.db.tenancy.tenant_session`, which makes the requirement
    explicit at the call site.
    """
    factory = await get_session_factory(settings)
    async with factory() as session, session.begin():
        yield session
