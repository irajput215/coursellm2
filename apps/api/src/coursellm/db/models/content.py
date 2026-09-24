"""Content models: courses, documents, chunks and the retrieval indexes.

This module is the physical expression of three decisions documented in
``docs/archive``-adjacent ADRs:

* **ADR-0002** — one PostgreSQL instance serves relational, vector and lexical
  access.
* **ADR-0006** — embeddings live in their own table keyed by
  ``(chunk_id, embedding_model, dim)``, so a vector space is part of the key and
  a model upgrade is a migration rather than silent corruption.
* **ADR-0003** — BM25 statistics are materialised per tenant, because IDF must be
  computed over the corpus a tenant is allowed to retrieve, not the global corpus.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coursellm.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

# ---------------------------------------------------------------------------
# Schema-level embedding dimension.
#
# pgvector requires a fixed dimension on a column in order to build an HNSW
# index, so the dimension is part of the physical schema and cannot be read from
# configuration at query time. ``EMBEDDING_DIM_SCHEMA_VERSION`` is asserted
# against ``settings.embedding_dim`` at startup: changing the configured model
# without writing a migration that rebuilds this column is a startup failure,
# not a runtime surprise.
# ---------------------------------------------------------------------------
EMBEDDING_DIM = 384


class DocumentStatus(StrEnum):
    """Lifecycle of a document through the ingestion pipeline."""

    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class QuarantineState(StrEnum):
    """Ingestion-time safety verdict for a document.

    ``FLAGGED`` documents are ingested but their content is treated as
    higher-risk evidence and is always placed in the untrusted region of a
    prompt. ``QUARANTINED`` documents are excluded from retrieval entirely until
    a human clears them.
    """

    CLEAN = "clean"
    FLAGGED = "flagged"
    QUARANTINED = "quarantined"


class SourceType(StrEnum):
    """Provenance class, used for trust labelling in recommendations.

    Distinct from the file extension: a PDF can be a lecture, a paper or a book
    chapter, and the distinction changes how the source should be presented and
    weighted.
    """

    LECTURE = "lecture"
    SLIDE = "slide"
    PAPER = "paper"
    BOOK = "book"
    SYLLABUS = "syllabus"
    NOTES = "notes"
    DOCUMENTATION = "documentation"
    OTHER = "other"


class Course(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A subject a student is studying. The unit documents are grouped by."""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "name", name="uq_courses_tenant_user_name"),
        Index("ix_courses_tenant_id_created_at", "tenant_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    documents: Mapped[list[Document]] = relationship(
        back_populates="course", cascade="all, delete-orphan", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<Course {self.name!r}>"


class Document(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An ingested source file and its ingestion provenance.

    Deduplication is by content hash, not by filename. Two students uploading
    ``notes.pdf`` are different documents; the same student re-uploading the same
    bytes is the same document, and re-ingestion must be idempotent rather than
    additive.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "course_id", "sha256", name="uq_documents_tenant_course_sha"),
        Index("ix_documents_tenant_id_status", "tenant_id", "status"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint(
            "injection_score >= 0 AND injection_score <= 1",
            name="injection_score_in_unit_interval",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ``filename`` is what the student called it and is only ever displayed.
    # ``storage_key`` is the server-generated name actually used on disk or in
    # object storage, which is what makes path traversal impossible rather than
    # merely filtered.
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)

    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_type: Mapped[SourceType] = mapped_column(
        pg_enum(SourceType),
        nullable=False,
        default=SourceType.OTHER,
        server_default=SourceType.OTHER.value,
    )

    status: Mapped[DocumentStatus] = mapped_column(
        pg_enum(DocumentStatus),
        nullable=False,
        default=DocumentStatus.PENDING,
        server_default=DocumentStatus.PENDING.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance for trust decisions. Populated at ingest time by the ingestion
    # safety scan, and surfaced to the user so a flagged source is visible rather
    # than silently trusted.
    injection_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    injection_classes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    quarantine_state: Mapped[QuarantineState] = mapped_column(
        pg_enum(QuarantineState),
        nullable=False,
        default=QuarantineState.CLEAN,
        server_default=QuarantineState.CLEAN.value,
    )

    # Ingestion configuration that produced this document's chunks. Recorded so
    # that a chunking change can be applied selectively rather than requiring a
    # full re-ingest of every document in the system.
    chunking_config_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    course: Mapped[Course] = relationship(back_populates="documents", lazy="raise")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<Document {self.filename!r} {self.status}>"


class Chunk(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A retrievable passage.

    ``token_count`` is stored rather than computed because BM25's length
    normalisation needs it on every query, and recomputing a token count for
    every candidate row would dominate the cost of lexical retrieval.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_chunk_index"),
        Index("ix_chunks_tenant_course", "tenant_id", "course_id"),
        Index("ix_chunks_tenant_document", "tenant_id", "document_id"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised from the document so that a course filter never needs a join
    # on the hot retrieval path. A unique constraint on
    # (document_id, chunk_index) does not prevent drift of course_id, so the
    # ingestion pipeline sets both in one statement and a consistency test
    # asserts they agree.
    course_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # True when this chunk begins mid-sentence because of a hard split, which
    # tells the context assembler that it may be worth merging with a neighbour.
    starts_mid_sentence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")
    embeddings: Mapped[list[ChunkEmbedding]] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", lazy="raise"
    )
    terms: Mapped[list[ChunkTerm]] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<Chunk doc={self.document_id} #{self.chunk_index}>"


class ChunkEmbedding(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A vector for a chunk, tagged with the model that produced it.

    The unique key is ``(chunk_id, embedding_model, dim)``. Retrieval filters on
    ``embedding_model``, so a query embedded by model *B* can never be compared
    against vectors produced by model *A* — the two would be numerically
    comparable and semantically meaningless, which is the worst kind of bug
    because it returns confident nonsense rather than an error.
    """

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "embedding_model", "dim", name="uq_chunk_embeddings_chunk_model_dim"
        ),
        Index(
            "ix_chunk_embeddings_tenant_model",
            "tenant_id",
            "embedding_model",
        ),
        # The ANN index. m and ef_construction are pgvector's defaults, stated
        # explicitly so that a change is a reviewable diff rather than an
        # invisible default shift. hnsw.ef_search is a session setting and is the
        # recall/latency knob that does not require a rebuild.
        Index(
            "ix_chunk_embeddings_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    chunk: Mapped[Chunk] = relationship(back_populates="embeddings", lazy="raise")

    def __repr__(self) -> str:
        return f"<ChunkEmbedding chunk={self.chunk_id} model={self.embedding_model}>"


class ChunkTerm(Base):
    """Per-chunk term frequency, the raw material of BM25.

    Keyed by ``(tenant_id, term)`` in addition to the primary key because the
    lexical query is always "which chunks in *this tenant* contain any of these
    terms". That predicate is served by an index rather than a scan, and BM25
    then rescores only the matched candidates.

    A stored Bag-of-words table rather than a ``tsvector`` because PostgreSQL's
    ``ts_rank`` is not BM25 — it has no term-frequency saturation, no length
    normalisation and no corpus-level IDF. Analysing in application code also
    guarantees that index-time and query-time tokenisation are identical, which
    a database text-search configuration makes easy to get subtly wrong.
    """

    __tablename__ = "chunk_terms"
    __table_args__ = (
        Index("ix_chunk_terms_tenant_term", "tenant_id", "term"),
        CheckConstraint("tf > 0", name="tf_positive"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    term: Mapped[str] = mapped_column(String(120), primary_key=True)
    tf: Mapped[int] = mapped_column(Integer, nullable=False)

    chunk: Mapped[Chunk] = relationship(back_populates="terms", lazy="raise")

    def __repr__(self) -> str:
        return f"<ChunkTerm {self.term!r} tf={self.tf}>"


class TenantLexicalStats(Base):
    """Document frequency per term, per tenant.

    Per-tenant rather than global for two reasons. Correctness: IDF must describe
    the corpus retrieval can actually return. Confidentiality: a global document
    frequency would leak the vocabulary of other tenants into scores a tenant can
    observe.
    """

    __tablename__ = "tenant_lexical_stats"
    __table_args__ = (CheckConstraint("doc_freq > 0", name="doc_freq_positive"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    term: Mapped[str] = mapped_column(String(120), primary_key=True)
    doc_freq: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<TenantLexicalStats {self.term!r} df={self.doc_freq}>"


class TenantCorpusStats(Base):
    """Corpus-level constants for BM25: N, total tokens and mean length.

    Materialised rather than aggregated at query time. ``avgdl`` appears in the
    denominator of every term of every candidate's score, so recomputing it per
    query would mean a full scan of the tenant's chunks on the hot path.
    """

    __tablename__ = "tenant_corpus_stats"
    __table_args__ = (
        CheckConstraint("doc_count >= 0", name="doc_count_non_negative"),
        CheckConstraint("total_tokens >= 0", name="total_tokens_non_negative"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    avg_doc_len: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    updated_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return f"<TenantCorpusStats docs={self.doc_count} avgdl={self.avg_doc_len:.1f}>"
