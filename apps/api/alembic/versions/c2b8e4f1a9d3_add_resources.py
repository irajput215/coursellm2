"""add the curated resource catalogue: resources and resource_concepts

Creates the two global tables behind PR 12's recommendations. Neither is
tenant-scoped and this migration deliberately does **not** enable Row-Level
Security on them.

``resources`` is a curated, human-reviewed catalogue of public learning
material. It is identical for every tenant, so duplicating it per tenant would be
wrong: the catalogue would diverge, and a review would have to be repeated. It
therefore carries no ``tenant_id``. ``resource_concepts`` joins a global resource
to a ``concepts.slug`` by value — there is no foreign key to the tenant-scoped
``concepts`` table and no ``tenant_id`` — so it is global too. Both are listed in
``coursellm.db.base.GLOBAL_TABLES`` and asserted by
``apps/api/tests/security/test_tenancy_boundary.py``.

The migration seeds nothing. Seeding is a separate, idempotent operation
(``coursellm.recommend.seed``, invoked by ``coursellm seed-catalogue`` or
``make seed-catalogue``) so that a schema revision is never also a data import,
and re-seeding on ``url`` never overwrites a reviewed row's metadata.

``difficulty`` is an integer with a ``BETWEEN 1 AND 5`` check constraint rather
than an enum type: the ranking interpolates it against a computed target, and an
ordered 1-5 scale gains nothing from a PostgreSQL enum.

Revision ID: c2b8e4f1a9d3
Revises: a7d3e91c4f28
Create Date: 2026-09-25 04:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2b8e4f1a9d3"
down_revision: str | None = "a7d3e91c4f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "authors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "resource_type",
            sa.Enum(
                "course",
                "mooc",
                "book",
                "documentation",
                "paper",
                "tutorial",
                "video",
                "standard",
                name="resourcetype",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column(
            "trust",
            sa.Enum(
                "official",
                "academic",
                "community",
                "secondary",
                name="sourcetrust",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("duration_hours", sa.Float(), nullable=True),
        sa.Column("is_free", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("rating", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(description) BETWEEN 1 AND 4000",
            name=op.f("ck_resources_description_len"),
        ),
        sa.CheckConstraint(
            "difficulty BETWEEN 1 AND 5", name=op.f("ck_resources_difficulty_range")
        ),
        sa.CheckConstraint(
            "duration_hours IS NULL OR duration_hours >= 0",
            name=op.f("ck_resources_duration_non_negative"),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 0 AND rating <= 5)",
            name=op.f("ck_resources_rating_range"),
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 400", name=op.f("ck_resources_title_len")
        ),
        sa.CheckConstraint(
            "year IS NULL OR year BETWEEN 1800 AND 2200", name=op.f("ck_resources_year_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
        sa.UniqueConstraint("url", name="uq_resources_url"),
    )
    op.create_index("idx_resources_resource_type", "resources", ["resource_type"], unique=False)
    op.create_index("idx_resources_trust", "resources", ["trust"], unique=False)
    op.create_index(
        "idx_resources_title_trgm",
        "resources",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )

    op.create_table(
        "resource_concepts",
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("concept_slug", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "concept_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name=op.f("ck_resource_concepts_concept_slug_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            name=op.f("fk_resource_concepts_resource_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("resource_id", "concept_slug", name=op.f("pk_resource_concepts")),
    )
    op.create_index(
        "idx_resource_concepts_slug", "resource_concepts", ["concept_slug"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_resource_concepts_slug", table_name="resource_concepts")
    op.drop_table("resource_concepts")

    op.drop_index("idx_resources_title_trgm", table_name="resources", postgresql_using="gin")
    op.drop_index("idx_resources_trust", table_name="resources")
    op.drop_index("idx_resources_resource_type", table_name="resources")
    op.drop_table("resources")
