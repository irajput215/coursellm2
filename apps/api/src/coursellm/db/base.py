"""Declarative base, naming conventions and reusable column mixins.

Two decisions here carry most of the weight.

**Explicit constraint naming.** PostgreSQL auto-names constraints, and those
generated names are not stable across versions or across the order in which
migrations were applied. Alembic autogenerate then produces migrations that
cannot be applied to a database built in a different order. A fixed
``naming_convention`` makes every index, foreign key and check constraint
predictable and therefore droppable by name.

**Tenancy as a mixin, not a convention.** ``TenantScopedMixin`` puts ``tenant_id``
on the model and in an index. A model that forgets it is visibly different from
one that has it, and the repository layer can assert on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Deterministic names for every implicitly-created constraint. Without this,
# Alembic cannot reliably drop a constraint it did not explicitly name.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pg_enum(enum_cls: type[PyEnum], *, length: int = 20) -> Enum:
    """Build a VARCHAR-backed enum column type that stores member *values*.

    By default SQLAlchemy persists the member **name** (``MEMBER``), while
    ``server_default=UserRole.MEMBER.value`` supplies the **value** (``member``).
    The two disagree, so a row inserted without an explicit value fails its CHECK
    constraint — a bug that only appears on the insert path and is easy to miss
    in review.

    ``native_enum=False`` renders as VARCHAR plus a CHECK constraint rather than a
    PostgreSQL ``ENUM`` type. Adding a member to a native PG enum requires
    ``ALTER TYPE``, which cannot run inside some transaction contexts and cannot
    be rolled back cleanly; a CHECK constraint is a plain, reviewable migration.
    ``values_callable`` is what aligns storage with the declared values.
    """
    return Enum(
        enum_cls,
        name=enum_cls.__name__.lower(),
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class UUIDPrimaryKeyMixin:
    """A UUID surrogate primary key.

    UUIDs rather than bigints because identifiers appear in URLs and API
    responses. Sequential integers leak record counts and creation order, and
    make cross-tenant existence probing trivial.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the database.

    ``server_default``/``onupdate`` are used rather than Python defaults so that
    rows written by migrations, background workers or manual SQL are also
    stamped correctly.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantScopedMixin:
    """Adds the tenancy boundary column.

    ``tenant_id`` is non-nullable and indexed on every tenant-scoped table.
    It is the column that Row-Level Security policies key on, and the column the
    repository layer refuses to build a query without.
    """

    @declared_attr
    @classmethod
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PGUUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


# Tables that carry ``tenant_id`` and are therefore protected by a
# Row-Level Security policy. Kept in one place so the migration that enables RLS
# and the test that asserts it is enabled cannot drift apart.
TENANT_SCOPED_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "courses",
        "documents",
        "chunks",
        "chunk_embeddings",
        "chunk_terms",
        "tenant_lexical_stats",
        "tenant_corpus_stats",
        "conversations",
        "messages",
        "refresh_token_revocations",
    }
)

# Tables intentionally outside the tenancy boundary. ``tenants`` is the root of
# the hierarchy: a policy on it would make tenant creation impossible, so access
# is restricted by the repository layer instead. This set is asserted by a test,
# because an accidental addition here would silently disable isolation.
GLOBAL_TABLES: frozenset[str] = frozenset({"tenants", "alembic_version"})
