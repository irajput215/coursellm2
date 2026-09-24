"""initial schema with tenancy, content and retrieval indexes

Creates every core table, then applies two things that autogenerate cannot
express and that must not be forgotten:

1. **Row-Level Security** on every tenant-scoped table, keyed on the
   ``app.tenant_id`` session variable.
2. **A narrow ``SECURITY DEFINER`` lookup** used only by the login path.

Revision ID: 9540db2f8c9a
Revises:
Create Date: 2026-09-25 00:15:03.223382
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9540db2f8c9a"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables carrying tenant_id, and therefore protected by a policy. This list is
# duplicated from coursellm.db.base.TENANT_SCOPED_TABLES on purpose: a migration
# must describe the schema as it was at this revision, and importing application
# code into a migration makes the migration change meaning when the code does.
TENANT_SCOPED_TABLES = (
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
)

TENANT_GUC = "app.tenant_id"


def _enable_rls() -> None:
    """Turn on Row-Level Security and give every table an isolation policy.

    The policy wraps the setting in ``NULLIF(..., '')`` before casting. That is not
    cosmetic. ``set_config(name, value, is_local => true)`` resets a custom variable
    to the **empty string** when the transaction ends, not to NULL, so
    ``current_setting(..., true)::uuid`` raises ``invalid input syntax for type
    uuid: ""`` on the first unscoped query served by a reused pooled connection.
    Without the ``NULLIF``, "fails closed" is really "fails loudly": correct in
    effect, but a 500 on every request that follows a tenant-scoped one. With it,
    the value is NULL when there is no context, the comparison is NULL, and the
    row is filtered -- which is the intended behaviour.

    ``FORCE ROW LEVEL SECURITY`` makes the policy apply to the table owner too.

    Without it the owner -- which is the role a single-role deployment naturally
    uses -- bypasses every policy, and isolation is believed rather than present.
    """
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


def _create_auth_lookup() -> None:
    """Create a minimal, auditable exception to the isolation policy.

    Login must find a user by email *before* a tenant is known. Under RLS that
    read returns nothing, so authentication cannot work. The alternatives were
    worse:

    * Give the application role ``BYPASSRLS`` -- one grant away from no isolation
      anywhere in the system.
    * Make ``users`` global -- removes the boundary from the table holding
      credentials.
    * Require a tenant slug at login -- leaks whether a tenant exists, and adds a
      step to every sign-in.

    Instead, one function is ``SECURITY DEFINER`` (it runs as its owner, who is
    not subject to the policy) and returns only the columns authentication
    requires. ``SET search_path = pg_catalog, public`` prevents a caller from
    shadowing ``users`` with a temp table to redirect the lookup, which is the
    standard escalation route for SECURITY DEFINER functions.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth_login_lookup(p_email text)
        RETURNS TABLE (
            user_id uuid,
            tenant_id uuid,
            role text,
            hashed_password text,
            is_active boolean,
            tenant_is_active boolean
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT u.id, u.tenant_id, u.role::text, u.hashed_password, u.is_active, t.is_active
            FROM users u
            JOIN tenants t ON t.id = u.tenant_id
            WHERE u.email = lower(btrim(p_email))
            LIMIT 1
        $$
        """
    )


def _drop_auth_lookup() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_login_lookup(text)")


def upgrade() -> None:
    # pgvector must exist before any Vector column is created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "tenants",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column(
            "plan",
            sa.Enum("free", "pro", "enterprise", name="tenantplan", native_enum=False, length=20),
            server_default="free",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "tenant_corpus_stats",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("doc_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_doc_len", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "doc_count >= 0", name=op.f("ck_tenant_corpus_stats_doc_count_non_negative")
        ),
        sa.CheckConstraint(
            "total_tokens >= 0", name=op.f("ck_tenant_corpus_stats_total_tokens_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_corpus_stats_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_tenant_corpus_stats")),
    )
    op.create_table(
        "tenant_lexical_stats",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("term", sa.String(length=120), nullable=False),
        sa.Column("doc_freq", sa.Integer(), nullable=False),
        sa.CheckConstraint("doc_freq > 0", name=op.f("ck_tenant_lexical_stats_doc_freq_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_lexical_stats_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "term", name=op.f("pk_tenant_lexical_stats")),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "member", name="userrole", native_enum=False, length=20),
            server_default="member",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_users_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=False)
    op.create_index("ix_users_tenant_id_role", "users", ["tenant_id", "role"], unique=False)
    op.create_table(
        "courses",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_courses_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_courses_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courses")),
        sa.UniqueConstraint("tenant_id", "user_id", "name", name="uq_courses_tenant_user_name"),
    )
    op.create_index(op.f("ix_courses_tenant_id"), "courses", ["tenant_id"], unique=False)
    op.create_index(
        "ix_courses_tenant_id_created_at", "courses", ["tenant_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_courses_user_id"), "courses", ["user_id"], unique=False)
    op.create_table(
        "refresh_token_revocations",
        sa.Column("jti", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_refresh_token_revocations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_token_revocations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_token_revocations")),
        sa.UniqueConstraint("jti", name="uq_refresh_token_revocations_jti"),
    )
    op.create_index(
        "ix_refresh_token_revocations_expires_at",
        "refresh_token_revocations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_token_revocations_tenant_id"),
        "refresh_token_revocations",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_token_revocations_user_id"),
        "refresh_token_revocations",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "conversations",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_conversations_course_id_courses"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        op.f("ix_conversations_course_id"), "conversations", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_conversations_tenant_user_updated",
        "conversations",
        ["tenant_id", "user_id", "updated_at"],
        unique=False,
    )
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum(
                "lecture",
                "slide",
                "paper",
                "book",
                "syllabus",
                "notes",
                "documentation",
                "other",
                name="sourcetype",
                native_enum=False,
                length=20,
            ),
            server_default="other",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "parsing",
                "chunking",
                "embedding",
                "indexing",
                "ready",
                "failed",
                name="documentstatus",
                native_enum=False,
                length=20,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("injection_score", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "injection_classes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "quarantine_state",
            sa.Enum(
                "clean",
                "flagged",
                "quarantined",
                name="quarantinestate",
                native_enum=False,
                length=20,
            ),
            server_default="clean",
            nullable=False,
        ),
        sa.Column("chunking_config_version", sa.String(length=32), nullable=True),
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
            "injection_score >= 0 AND injection_score <= 1",
            name=op.f("ck_documents_injection_score_in_unit_interval"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_documents_size_bytes_non_negative")),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_documents_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_documents_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_documents_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_documents_storage_key")),
        sa.UniqueConstraint(
            "tenant_id", "course_id", "sha256", name="uq_documents_tenant_course_sha"
        ),
    )
    op.create_index(op.f("ix_documents_course_id"), "documents", ["course_id"], unique=False)
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False)
    op.create_index(
        "ix_documents_tenant_id_status", "documents", ["tenant_id", "status"], unique=False
    )
    op.create_index(op.f("ix_documents_user_id"), "documents", ["user_id"], unique=False)
    op.create_table(
        "chunks",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=True),
        sa.Column("starts_mid_sentence", sa.Boolean(), server_default="false", nullable=False),
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
        sa.CheckConstraint("chunk_index >= 0", name=op.f("ck_chunks_chunk_index_non_negative")),
        sa.CheckConstraint("token_count > 0", name=op.f("ck_chunks_token_count_positive")),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_chunks_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_chunks_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_chunk_index"),
    )
    op.create_index(op.f("ix_chunks_course_id"), "chunks", ["course_id"], unique=False)
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False)
    op.create_index("ix_chunks_tenant_course", "chunks", ["tenant_id", "course_id"], unique=False)
    op.create_index(
        "ix_chunks_tenant_document", "chunks", ["tenant_id", "document_id"], unique=False
    )
    op.create_index(op.f("ix_chunks_tenant_id"), "chunks", ["tenant_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "system",
                "user",
                "assistant",
                "tool",
                name="messagerole",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
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
        sa.Column("grounded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retrieval_config_version", sa.String(length=32), nullable=True),
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
        sa.CheckConstraint("token_count >= 0", name=op.f("ck_messages_token_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_messages_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False
    )
    op.create_index(
        "ix_messages_tenant_conversation_created",
        "messages",
        ["tenant_id", "conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_messages_tenant_id"), "messages", ["tenant_id"], unique=False)
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_chunk_embeddings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint(
            "chunk_id", "embedding_model", "dim", name="uq_chunk_embeddings_chunk_model_dim"
        ),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_chunk_id"), "chunk_embeddings", ["chunk_id"], unique=False
    )
    op.create_index(
        "ix_chunk_embeddings_hnsw_cosine",
        "chunk_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_chunk_embeddings_tenant_id"), "chunk_embeddings", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_chunk_embeddings_tenant_model",
        "chunk_embeddings",
        ["tenant_id", "embedding_model"],
        unique=False,
    )
    op.create_table(
        "chunk_terms",
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("term", sa.String(length=120), nullable=False),
        sa.Column("tf", sa.Integer(), nullable=False),
        sa.CheckConstraint("tf > 0", name=op.f("ck_chunk_terms_tf_positive")),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_terms_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_chunk_terms_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", "term", name=op.f("pk_chunk_terms")),
    )
    op.create_index(
        "ix_chunk_terms_tenant_term", "chunk_terms", ["tenant_id", "term"], unique=False
    )

    # ### end Alembic commands ###

    # Isolation first, then the single narrow exception to it.
    _enable_rls()
    _create_auth_lookup()


def downgrade() -> None:
    # Reverse order of upgrade: drop the exception before the policies.
    _drop_auth_lookup()
    _disable_rls()

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_chunk_terms_tenant_term", table_name="chunk_terms")
    op.drop_table("chunk_terms")
    op.drop_index("ix_chunk_embeddings_tenant_model", table_name="chunk_embeddings")
    op.drop_index(op.f("ix_chunk_embeddings_tenant_id"), table_name="chunk_embeddings")
    op.drop_index(
        "ix_chunk_embeddings_hnsw_cosine",
        table_name="chunk_embeddings",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_chunk_embeddings_chunk_id"), table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index(op.f("ix_messages_tenant_id"), table_name="messages")
    op.drop_index("ix_messages_tenant_conversation_created", table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_chunks_tenant_id"), table_name="chunks")
    op.drop_index("ix_chunks_tenant_document", table_name="chunks")
    op.drop_index("ix_chunks_tenant_course", table_name="chunks")
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_course_id"), table_name="chunks")
    op.drop_table("chunks")
    op.drop_index(op.f("ix_documents_user_id"), table_name="documents")
    op.drop_index("ix_documents_tenant_id_status", table_name="documents")
    op.drop_index(op.f("ix_documents_tenant_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_course_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_index("ix_conversations_tenant_user_updated", table_name="conversations")
    op.drop_index(op.f("ix_conversations_tenant_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_course_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(
        op.f("ix_refresh_token_revocations_user_id"), table_name="refresh_token_revocations"
    )
    op.drop_index(
        op.f("ix_refresh_token_revocations_tenant_id"), table_name="refresh_token_revocations"
    )
    op.drop_index("ix_refresh_token_revocations_expires_at", table_name="refresh_token_revocations")
    op.drop_table("refresh_token_revocations")
    op.drop_index(op.f("ix_courses_user_id"), table_name="courses")
    op.drop_index("ix_courses_tenant_id_created_at", table_name="courses")
    op.drop_index(op.f("ix_courses_tenant_id"), table_name="courses")
    op.drop_table("courses")
    op.drop_index("ix_users_tenant_id_role", table_name="users")
    op.drop_index(op.f("ix_users_tenant_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("tenant_lexical_stats")
    op.drop_table("tenant_corpus_stats")
    op.drop_table("tenants")
