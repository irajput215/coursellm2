"""add learning: roadmaps, ordered steps, progress events and quiz attempts

Creates the four learning tables, then applies what autogenerate cannot express:
Row-Level Security on each of them, keyed on the ``app.tenant_id`` session
variable with the same ``NULLIF(..., '')`` guard as every other tenant-scoped
table (see the initial migration for why the guard is not cosmetic).

Two schema decisions are visible here and documented in
``docs/architecture/knowledge-graph.md`` §8 and ``docs/architecture/system.md`` §8:

* ``progress_events`` is append-only and ``roadmaps`` is versioned. Mastery is a
  projection over the event log and a changed plan is a new ``roadmaps`` row with
  the previous one marked ``superseded``, so both histories stay readable.
* ``roadmap_steps.blocked_by`` stores concept ids as JSONB rather than a join
  table. The set is small, always read with the step, and describes the
  curriculum rather than one revision's row ids.

The table list is duplicated from ``coursellm.db.base.TENANT_SCOPED_TABLES`` on
purpose: a migration must describe the schema as it was at this revision, and
importing application code would make an old migration change meaning when the
code does.

Revision ID: a7d3e91c4f28
Revises: b3f1a72c9d40
Create Date: 2026-09-25 03:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d3e91c4f28"
down_revision: str | None = "b3f1a72c9d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only the tables added by this revision. The security test unions this list
# with the earlier migrations and compares it to the code's list.
TENANT_SCOPED_TABLES = (
    "progress_events",
    "quiz_attempts",
    "roadmaps",
    "roadmap_steps",
)

TENANT_GUC = "app.tenant_id"


def _enable_rls() -> None:
    """Turn on Row-Level Security and add the isolation policy to each table."""
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
    # roadmaps is created before roadmap_steps, which references it.
    op.create_table(
        "roadmaps",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=True),
        sa.Column("goal_concept_id", sa.UUID(), nullable=True),
        sa.Column("goal_text", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "superseded",
                "completed",
                name="roadmapstatus",
                native_enum=False,
                length=20,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("estimated_hours", sa.Float(), server_default="0", nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(goal_text) BETWEEN 1 AND 500", name=op.f("ck_roadmaps_goal_text_len")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_roadmaps_revision_positive")),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'completed')",
            name=op.f("ck_roadmaps_status_known"),
        ),
        sa.CheckConstraint(
            "estimated_hours >= 0", name=op.f("ck_roadmaps_estimated_hours_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_roadmaps_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["goal_concept_id"],
            ["concepts.id"],
            name=op.f("fk_roadmaps_goal_concept_id_concepts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_roadmaps_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_roadmaps_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roadmaps")),
    )
    op.create_index(op.f("ix_roadmaps_course_id"), "roadmaps", ["course_id"], unique=False)
    op.create_index(
        op.f("ix_roadmaps_goal_concept_id"), "roadmaps", ["goal_concept_id"], unique=False
    )
    op.create_index(op.f("ix_roadmaps_tenant_id"), "roadmaps", ["tenant_id"], unique=False)
    op.create_index(
        "ix_roadmaps_tenant_user_created",
        "roadmaps",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_roadmaps_tenant_user_goal",
        "roadmaps",
        ["tenant_id", "user_id", "goal_concept_id"],
        unique=False,
    )
    op.create_index(op.f("ix_roadmaps_user_id"), "roadmaps", ["user_id"], unique=False)

    op.create_table(
        "roadmap_steps",
        sa.Column("roadmap_id", sa.UUID(), nullable=False),
        sa.Column("concept_id", sa.UUID(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "available",
                "in_progress",
                "completed",
                "blocked",
                name="roadmapstepstatus",
                native_enum=False,
                length=20,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "blocked_by",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("estimated_hours", sa.Float(), server_default="0", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 300", name=op.f("ck_roadmap_steps_title_len")
        ),
        sa.CheckConstraint(
            "estimated_hours >= 0", name=op.f("ck_roadmap_steps_estimated_hours_non_negative")
        ),
        sa.CheckConstraint(
            "order_index >= 0", name=op.f("ck_roadmap_steps_order_index_non_negative")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'available', 'in_progress', 'completed', 'blocked')",
            name=op.f("ck_roadmap_steps_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name=op.f("fk_roadmap_steps_concept_id_concepts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["roadmap_id"],
            ["roadmaps.id"],
            name=op.f("fk_roadmap_steps_roadmap_id_roadmaps"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_roadmap_steps_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roadmap_steps")),
        sa.UniqueConstraint("roadmap_id", "order_index", name="uq_roadmap_steps_roadmap_order"),
    )
    op.create_index(
        op.f("ix_roadmap_steps_concept_id"), "roadmap_steps", ["concept_id"], unique=False
    )
    op.create_index(
        op.f("ix_roadmap_steps_roadmap_id"), "roadmap_steps", ["roadmap_id"], unique=False
    )
    op.create_index(
        op.f("ix_roadmap_steps_tenant_id"), "roadmap_steps", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_roadmap_steps_tenant_roadmap",
        "roadmap_steps",
        ["tenant_id", "roadmap_id"],
        unique=False,
    )

    op.create_table(
        "progress_events",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=True),
        sa.Column("concept_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "quiz_attempt",
                "topic_completed",
                "concept_mastered",
                "concept_struggled",
                "roadmap_step_completed",
                name="progresseventkind",
                native_enum=False,
                length=32,
            ),
            server_default="topic_completed",
            nullable=False,
        ),
        sa.Column("mastery", sa.Float(), server_default="0", nullable=False),
        sa.Column("weight", sa.Float(), server_default="1", nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=80), server_default="unknown", nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('quiz_attempt', 'topic_completed', 'concept_mastered', "
            "'concept_struggled', 'roadmap_step_completed')",
            name=op.f("ck_progress_events_kind_known"),
        ),
        sa.CheckConstraint(
            "mastery >= 0 AND mastery <= 1", name=op.f("ck_progress_events_mastery_unit")
        ),
        sa.CheckConstraint("weight >= 0", name=op.f("ck_progress_events_weight_non_negative")),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name=op.f("fk_progress_events_concept_id_concepts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_progress_events_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_progress_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_progress_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_progress_events")),
    )
    op.create_index(
        op.f("ix_progress_events_concept_id"), "progress_events", ["concept_id"], unique=False
    )
    op.create_index(
        op.f("ix_progress_events_course_id"), "progress_events", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_progress_events_tenant_id"), "progress_events", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_progress_events_tenant_user_concept",
        "progress_events",
        ["tenant_id", "user_id", "concept_id"],
        unique=False,
    )
    op.create_index(
        "ix_progress_events_tenant_user_occurred",
        "progress_events",
        ["tenant_id", "user_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_progress_events_user_id"), "progress_events", ["user_id"], unique=False
    )

    op.create_table(
        "quiz_attempts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("quiz_id", sa.UUID(), nullable=True),
        sa.Column("item_id", sa.String(length=120), nullable=False),
        sa.Column(
            "concept_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "rubric",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "misconceptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 1", name=op.f("ck_quiz_attempts_score_unit")),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_quiz_attempts_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_quiz_attempts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_quiz_attempts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quiz_attempts")),
    )
    op.create_index(
        op.f("ix_quiz_attempts_course_id"), "quiz_attempts", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_quiz_attempts_tenant_id"), "quiz_attempts", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_quiz_attempts_tenant_user_created",
        "quiz_attempts",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_quiz_attempts_user_id"), "quiz_attempts", ["user_id"], unique=False)

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index(op.f("ix_quiz_attempts_user_id"), table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_tenant_user_created", table_name="quiz_attempts")
    op.drop_index(op.f("ix_quiz_attempts_tenant_id"), table_name="quiz_attempts")
    op.drop_index(op.f("ix_quiz_attempts_course_id"), table_name="quiz_attempts")
    op.drop_table("quiz_attempts")

    op.drop_index(op.f("ix_progress_events_user_id"), table_name="progress_events")
    op.drop_index("ix_progress_events_tenant_user_occurred", table_name="progress_events")
    op.drop_index("ix_progress_events_tenant_user_concept", table_name="progress_events")
    op.drop_index(op.f("ix_progress_events_tenant_id"), table_name="progress_events")
    op.drop_index(op.f("ix_progress_events_course_id"), table_name="progress_events")
    op.drop_index(op.f("ix_progress_events_concept_id"), table_name="progress_events")
    op.drop_table("progress_events")

    op.drop_index("ix_roadmap_steps_tenant_roadmap", table_name="roadmap_steps")
    op.drop_index(op.f("ix_roadmap_steps_tenant_id"), table_name="roadmap_steps")
    op.drop_index(op.f("ix_roadmap_steps_roadmap_id"), table_name="roadmap_steps")
    op.drop_index(op.f("ix_roadmap_steps_concept_id"), table_name="roadmap_steps")
    op.drop_table("roadmap_steps")

    op.drop_index(op.f("ix_roadmaps_user_id"), table_name="roadmaps")
    op.drop_index("ix_roadmaps_tenant_user_goal", table_name="roadmaps")
    op.drop_index("ix_roadmaps_tenant_user_created", table_name="roadmaps")
    op.drop_index(op.f("ix_roadmaps_tenant_id"), table_name="roadmaps")
    op.drop_index(op.f("ix_roadmaps_goal_concept_id"), table_name="roadmaps")
    op.drop_index(op.f("ix_roadmaps_course_id"), table_name="roadmaps")
    op.drop_table("roadmaps")
