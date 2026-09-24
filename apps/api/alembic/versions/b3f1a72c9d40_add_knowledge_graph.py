"""add knowledge graph: concepts, aliases, edges and extraction runs

Creates the four graph tables, then applies the three things autogenerate cannot
express and that must not be forgotten:

1. **``pg_trgm``** for the concept-name similarity index.
2. **A trigger** that keeps ``concept_aliases.course_id`` in sync with its
   parent concept, because that denormalised column is part of the alias
   uniqueness constraint (``docs/architecture/knowledge-graph.md`` §3.2).
3. **Row-Level Security** on all four tables, keyed on the ``app.tenant_id``
   session variable, using the same ``NULLIF(..., '')`` guard as every other
   tenant-scoped table (see the initial migration for why the guard is not
   cosmetic).

The table list is duplicated from ``coursellm.db.base.TENANT_SCOPED_TABLES`` on
purpose: a migration must describe the schema as it was at this revision, and
importing application code would make an old migration change meaning when the
code does.

Revision ID: b3f1a72c9d40
Revises: 5b9bac36ed61
Create Date: 2026-09-25 02:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f1a72c9d40"
down_revision: str | None = "5b9bac36ed61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only the tables added by this revision. The security test unions this list
# with the earlier migrations and compares it to the code's list.
TENANT_SCOPED_TABLES = (
    "concepts",
    "concept_aliases",
    "concept_edges",
    "graph_extraction_runs",
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


def _create_alias_course_trigger() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION concept_aliases_sync_course()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            SELECT c.course_id INTO NEW.course_id
            FROM concepts c
            WHERE c.id = NEW.concept_id AND c.tenant_id = NEW.tenant_id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER concept_aliases_sync_course
            BEFORE INSERT OR UPDATE OF concept_id ON concept_aliases
            FOR EACH ROW EXECUTE FUNCTION concept_aliases_sync_course()
        """
    )


def _drop_alias_course_trigger() -> None:
    op.execute("DROP TRIGGER IF EXISTS concept_aliases_sync_course ON concept_aliases")
    op.execute("DROP FUNCTION IF EXISTS concept_aliases_sync_course()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # graphs_extraction_runs is created first: concepts and concept_edges both
    # carry a foreign key to it.
    op.create_table(
        "graph_extraction_runs",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("extraction_config_version", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                "partial",
                name="extractionrunstatus",
                native_enum=False,
                length=20,
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column("chunks_considered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("concepts_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("edges_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("edges_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("edges_queued_for_review", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "rejection_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "cost_usd", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'partial')",
            name=op.f("ck_graph_extraction_runs_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_graph_extraction_runs_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_graph_extraction_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_extraction_runs")),
    )
    op.create_index(
        op.f("ix_graph_extraction_runs_document_id"),
        "graph_extraction_runs",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graph_extraction_runs_tenant_id"),
        "graph_extraction_runs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "idx_graph_extraction_runs_document",
        "graph_extraction_runs",
        ["tenant_id", "document_id"],
        unique=False,
    )
    op.create_index(
        "idx_graph_extraction_runs_started",
        "graph_extraction_runs",
        ["tenant_id", "started_at"],
        unique=False,
    )

    op.create_table(
        "concepts",
        sa.Column("course_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.Integer(), server_default="3", nullable=False),
        sa.Column("provenance_document_id", sa.UUID(), nullable=True),
        sa.Column("provenance_chunk_id", sa.UUID(), nullable=True),
        sa.Column("provenance_page", sa.Integer(), nullable=True),
        sa.Column("extraction_run_id", sa.UUID(), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column(
            "confidence", sa.Numeric(precision=4, scale=3), server_default="0.000", nullable=False
        ),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_concepts_confidence_unit")
        ),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name=op.f("ck_concepts_difficulty")),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 2 AND 200", name=op.f("ck_concepts_name_len")
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_concepts_slug_shape")
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_concepts_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["graph_extraction_runs.id"],
            name=op.f("fk_concepts_extraction_run_id_graph_extraction_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provenance_chunk_id"],
            ["chunks.id"],
            name=op.f("fk_concepts_provenance_chunk_id_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provenance_document_id"],
            ["documents.id"],
            name=op.f("fk_concepts_provenance_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_concepts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concepts")),
        sa.UniqueConstraint(
            "tenant_id", "course_id", "slug", name="uq_concepts_tenant_course_slug"
        ),
    )
    op.create_index(op.f("ix_concepts_course_id"), "concepts", ["course_id"], unique=False)
    op.create_index(op.f("ix_concepts_tenant_id"), "concepts", ["tenant_id"], unique=False)
    op.create_index(
        "idx_concepts_tenant_course", "concepts", ["tenant_id", "course_id"], unique=False
    )
    op.create_index(
        "idx_concepts_name_trgm",
        "concepts",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "concept_aliases",
        sa.Column("course_id", sa.UUID(), nullable=True),
        sa.Column("concept_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("alias_norm", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "extraction",
                "human",
                name="aliassource",
                native_enum=False,
                length=20,
            ),
            server_default="extraction",
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
        sa.CheckConstraint(
            "source IN ('extraction', 'human')", name=op.f("ck_concept_aliases_source_known")
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name=op.f("fk_concept_aliases_concept_id_concepts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_concept_aliases_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_concept_aliases_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concept_aliases")),
        sa.UniqueConstraint(
            "tenant_id", "course_id", "alias_norm", name="concept_aliases_norm_unique"
        ),
    )
    op.create_index(
        op.f("ix_concept_aliases_concept_id"), "concept_aliases", ["concept_id"], unique=False
    )
    op.create_index(
        op.f("ix_concept_aliases_course_id"), "concept_aliases", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_concept_aliases_tenant_id"), "concept_aliases", ["tenant_id"], unique=False
    )
    op.create_index(
        "idx_concept_aliases_lookup",
        "concept_aliases",
        ["tenant_id", "course_id", "alias_norm"],
        unique=False,
    )

    op.create_table(
        "concept_edges",
        sa.Column("source_concept_id", sa.UUID(), nullable=False),
        sa.Column("target_concept_id", sa.UUID(), nullable=False),
        sa.Column(
            "relation",
            sa.Enum(
                "requires",
                "contains",
                "related_to",
                "part_of",
                "assesses",
                "taught_by",
                name="edgerelation",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "weight", sa.Numeric(precision=4, scale=3), server_default="1.000", nullable=False
        ),
        sa.Column(
            "confidence", sa.Numeric(precision=4, scale=3), server_default="0.000", nullable=False
        ),
        sa.Column("corroboration_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("cue", sa.String(length=20), server_default="inferred", nullable=False),
        sa.Column("provenance_document_id", sa.UUID(), nullable=True),
        sa.Column("provenance_chunk_id", sa.UUID(), nullable=True),
        sa.Column("provenance_page", sa.Integer(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column(
            "provenance_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("extraction_run_id", sa.UUID(), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_concept_edges_confidence_unit")
        ),
        sa.CheckConstraint(
            "corroboration_count >= 1", name=op.f("ck_concept_edges_corroboration_positive")
        ),
        sa.CheckConstraint(
            "cue IN ('explicit', 'inferred')", name=op.f("ck_concept_edges_cue_known")
        ),
        sa.CheckConstraint(
            "source_concept_id <> target_concept_id", name=op.f("ck_concept_edges_no_self_loop")
        ),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1", name=op.f("ck_concept_edges_weight_unit")
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["graph_extraction_runs.id"],
            name=op.f("fk_concept_edges_extraction_run_id_graph_extraction_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provenance_chunk_id"],
            ["chunks.id"],
            name=op.f("fk_concept_edges_provenance_chunk_id_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provenance_document_id"],
            ["documents.id"],
            name=op.f("fk_concept_edges_provenance_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_concept_id"],
            ["concepts.id"],
            name=op.f("fk_concept_edges_source_concept_id_concepts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_concept_id"],
            ["concepts.id"],
            name=op.f("fk_concept_edges_target_concept_id_concepts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_concept_edges_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concept_edges")),
        sa.UniqueConstraint(
            "tenant_id",
            "source_concept_id",
            "target_concept_id",
            "relation",
            name="concept_edges_unique",
        ),
    )
    op.create_index(
        op.f("ix_concept_edges_tenant_id"), "concept_edges", ["tenant_id"], unique=False
    )
    op.create_index(
        "idx_concept_edges_out",
        "concept_edges",
        ["tenant_id", "source_concept_id", "relation"],
        unique=False,
    )
    op.create_index(
        "idx_concept_edges_in",
        "concept_edges",
        ["tenant_id", "target_concept_id", "relation"],
        unique=False,
    )
    op.create_index(
        "idx_concept_edges_provenance",
        "concept_edges",
        ["provenance_document_id", "provenance_chunk_id"],
        unique=False,
    )
    op.create_index(
        "idx_concept_edges_traversable",
        "concept_edges",
        ["tenant_id", "source_concept_id"],
        unique=False,
        postgresql_where=sa.text("relation IN ('requires', 'part_of') AND confidence >= 0.6"),
    )
    op.create_index(
        "idx_concept_edges_review",
        "concept_edges",
        ["tenant_id", sa.text("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("verified = false AND confidence < 0.75"),
    )

    _create_alias_course_trigger()
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    _drop_alias_course_trigger()

    op.drop_index(
        "idx_concept_edges_review",
        table_name="concept_edges",
        postgresql_where=sa.text("verified = false AND confidence < 0.75"),
    )
    op.drop_index(
        "idx_concept_edges_traversable",
        table_name="concept_edges",
        postgresql_where=sa.text("relation IN ('requires', 'part_of') AND confidence >= 0.6"),
    )
    op.drop_index("idx_concept_edges_provenance", table_name="concept_edges")
    op.drop_index("idx_concept_edges_in", table_name="concept_edges")
    op.drop_index("idx_concept_edges_out", table_name="concept_edges")
    op.drop_index(op.f("ix_concept_edges_tenant_id"), table_name="concept_edges")
    op.drop_table("concept_edges")

    op.drop_index("idx_concept_aliases_lookup", table_name="concept_aliases")
    op.drop_index(op.f("ix_concept_aliases_tenant_id"), table_name="concept_aliases")
    op.drop_index(op.f("ix_concept_aliases_course_id"), table_name="concept_aliases")
    op.drop_index(op.f("ix_concept_aliases_concept_id"), table_name="concept_aliases")
    op.drop_table("concept_aliases")

    op.drop_index("idx_concepts_name_trgm", table_name="concepts", postgresql_using="gin")
    op.drop_index("idx_concepts_tenant_course", table_name="concepts")
    op.drop_index(op.f("ix_concepts_tenant_id"), table_name="concepts")
    op.drop_index(op.f("ix_concepts_course_id"), table_name="concepts")
    op.drop_table("concepts")

    op.drop_index("idx_graph_extraction_runs_started", table_name="graph_extraction_runs")
    op.drop_index("idx_graph_extraction_runs_document", table_name="graph_extraction_runs")
    op.drop_index(op.f("ix_graph_extraction_runs_tenant_id"), table_name="graph_extraction_runs")
    op.drop_index(op.f("ix_graph_extraction_runs_document_id"), table_name="graph_extraction_runs")
    op.drop_table("graph_extraction_runs")
