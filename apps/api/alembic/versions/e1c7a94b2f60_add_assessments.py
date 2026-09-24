"""add assessments: the persisted quiz draft

Creates ``quizzes`` and enables Row-Level Security on it with the same
``NULLIF(current_setting('app.tenant_id', true), '')::uuid`` policy as every
other tenant-scoped table (see the initial migration for why the guard is not
cosmetic).

The draft is one table with a JSONB ``items`` column rather than a child
``quiz_items`` table: an item is only ever read as part of its draft, has no
identity outside it, and is never queried independently. ``quiz_attempts``
already references the draft by a bare UUID so that a recorded grade survives
the deletion of the draft that produced it, so the reverse foreign key is
deliberately absent too.

The table list is duplicated from ``coursellm.db.base.TENANT_SCOPED_TABLES`` on
purpose: a migration must describe the schema as it was at this revision, and
importing application code would make an old migration change meaning when the
code does.

Revision ID: e1c7a94b2f60
Revises: c2b8e4f1a9d3
Create Date: 2026-09-25 06:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1c7a94b2f60"
down_revision: str | None = "c2b8e4f1a9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only the table added by this revision. The security test unions this list with
# the earlier migrations and compares it to the code's list.
TENANT_SCOPED_TABLES = ("quizzes",)

TENANT_GUC = "app.tenant_id"


def _enable_rls() -> None:
    """Turn on Row-Level Security and add the isolation policy."""
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid)
            """
        )


def _disable_rls() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "quizzes",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("difficulty", sa.String(length=20), server_default="medium", nullable=False),
        sa.Column("n_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("shortfall", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "concept_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "item_types",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "degraded",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name=op.f("ck_quizzes_difficulty_known"),
        ),
        sa.CheckConstraint("n_items >= 0", name=op.f("ck_quizzes_n_items_non_negative")),
        sa.CheckConstraint("shortfall >= 0", name=op.f("ck_quizzes_shortfall_non_negative")),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_quizzes_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_quizzes_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_quizzes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quizzes")),
    )
    op.create_index(op.f("ix_quizzes_course_id"), "quizzes", ["course_id"], unique=False)
    op.create_index(op.f("ix_quizzes_tenant_id"), "quizzes", ["tenant_id"], unique=False)
    op.create_index(
        "ix_quizzes_tenant_user_created",
        "quizzes",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_quizzes_user_id"), "quizzes", ["user_id"], unique=False)

    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index(op.f("ix_quizzes_user_id"), table_name="quizzes")
    op.drop_index("ix_quizzes_tenant_user_created", table_name="quizzes")
    op.drop_index(op.f("ix_quizzes_tenant_id"), table_name="quizzes")
    op.drop_index(op.f("ix_quizzes_course_id"), table_name="quizzes")
    op.drop_table("quizzes")
