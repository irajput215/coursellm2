"""The curated resource catalogue: a global, public, tenant-independent dataset.

**Not tenant-scoped, on purpose.** ``resources`` is a curated, human-reviewed
catalogue of public learning material — the same rows for every tenant. Making it
tenant-scoped would duplicate the identical catalogue per tenant, multiply the
review burden, and let one tenant's edits silently diverge from everyone else's.
Because the table is global it carries **no** ``tenant_id`` and is deliberately
absent from ``TENANT_SCOPED_TABLES``; it is listed in ``GLOBAL_TABLES`` and
``apps/api/tests/security/test_tenancy_boundary.py`` asserts both directions.

**Metadata is sourced, never invented.** Every seeded row must name a resource
that genuinely exists, with a canonical publisher, institution or project URL. The
seed-integrity test enforces a strict ``https`` URL and a domain allowlist;
omitting an uncertain entry is always preferred to guessing a URL.

``resource_concepts`` is global too: it joins a global resource to a
``concepts.slug`` by value (there is no cross-tenant foreign key), so it must not
carry ``tenant_id`` either.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from coursellm.db.base import Base, UUIDPrimaryKeyMixin, pg_enum
from coursellm.recommend.schemas import (
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    ResourceType,
    SourceTrust,
)


class Resource(UUIDPrimaryKeyMixin, Base):
    """One curated catalogue entry, with its provenance and coverage."""

    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("url", name="uq_resources_url"),
        CheckConstraint(
            f"difficulty BETWEEN {MIN_DIFFICULTY} AND {MAX_DIFFICULTY}", name="difficulty_range"
        ),
        CheckConstraint("year IS NULL OR year BETWEEN 1800 AND 2200", name="year_range"),
        CheckConstraint("rating IS NULL OR (rating >= 0 AND rating <= 5)", name="rating_range"),
        CheckConstraint(
            "duration_hours IS NULL OR duration_hours >= 0", name="duration_non_negative"
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 400", name="title_len"),
        CheckConstraint("char_length(description) BETWEEN 1 AND 4000", name="description_len"),
        Index("idx_resources_resource_type", "resource_type"),
        Index("idx_resources_trust", "trust"),
        Index(
            "idx_resources_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    # A list of human names, in the order the publisher states them. Stored as
    # JSONB because it is always read with the row and never queried by its
    # elements; a join table would add ceremony for no query benefit.
    authors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[ResourceType] = mapped_column(pg_enum(ResourceType), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    trust: Mapped[SourceTrust] = mapped_column(pg_enum(SourceTrust), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_free: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    # ``is_verified`` is an editorial assertion and is never set by seeding: the
    # seed only records that a URL was checked as reachable, not that a human
    # reviewed the material. Leaving it false is the honest default.
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Resource {self.title!r} ({self.resource_type})>"


class ResourceConcept(Base):
    """Which concepts a resource teaches. **Global** — no ``tenant_id``.

    ``concept_slug`` matches ``concepts.slug`` by value rather than by foreign
    key: ``concepts`` is tenant-scoped, and a global catalogue row must not point
    at one tenant's concept row. The slug is the shared, deterministic key that
    lets a tenant's extracted concepts align to the public vocabulary published by
    :mod:`coursellm.recommend.seed`.
    """

    __tablename__ = "resource_concepts"
    __table_args__ = (
        CheckConstraint("concept_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="concept_slug_shape"),
        Index("idx_resource_concepts_slug", "concept_slug"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("resources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    concept_slug: Mapped[str] = mapped_column(Text, primary_key=True)

    def __repr__(self) -> str:
        return f"<ResourceConcept {self.resource_id} -> {self.concept_slug!r}>"


__all__ = ["Resource", "ResourceConcept", "ResourceType", "SourceTrust"]
