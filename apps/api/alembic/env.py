"""Alembic environment.

Configured for an async engine, because the application uses ``asyncpg`` and a
second, synchronous driver would be one more thing to keep in step.

The database URL comes from application settings via
:func:`coursellm.core.config.get_settings`, not from ``alembic.ini``. That is
deliberate: an earlier revision of this project normalised the URL in the
application but not in Alembic, so migrations failed against the same database
the application was using.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from coursellm.core.config import get_settings

# Importing the models package registers every table on Base.metadata.
# Autogenerate compares against this metadata, so a model missing from
# ``coursellm.db.models`` would be silently absent from migrations.
from coursellm.db import models  # noqa: F401
from coursellm.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the URL, allowing an explicit override for migrations.

    ``ALEMBIC_DATABASE_URL`` exists so that a migration can be run by a
    privileged owner role while the application runs as a restricted role. That
    split matters: the application role must not be able to bypass Row-Level
    Security, but schema changes require ownership.
    """
    import os

    return os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Useful for review: a migration's effect can be read before it is applied.
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type and server-default drift. Without these, a model
        # change that only alters a type produces an empty migration and the
        # divergence is discovered in production.
        compare_type=True,
        compare_server_default=True,
        # Include the pgvector type so that autogenerate does not try to drop
        # every embedding column as an unknown type.
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
