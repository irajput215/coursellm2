"""Knowledge-graph models: concepts, aliases, typed edges and extraction runs.

The graph lives in PostgreSQL rather than a dedicated graph store
(``docs/decisions/ADR-0004`` and ``docs/architecture/knowledge-graph.md`` §2):
the working set is thousands of concepts and tens of thousands of edges per
tenant, ``WITH RECURSIVE`` expresses closure and depth limiting, and the same
transaction that writes ``documents``/``chunks`` writes the concept that cites
them, so provenance is a real foreign key rather than a cross-store promise.

Four decisions are visible in this module:

* **``slug`` is the deterministic key.** It is produced by normalisation, not by
  a model judgement, which is what makes deduplication and alias resolution
  mechanical instead of a second inference problem (§3.1).
* **Structure carries direction; confidence does not.** ``relation`` declares
  once how an edge is read (``requires`` means *source depends on target*), and
  every traversal repeats ``tenant_id`` so the composite indexes are usable.
* **Provenance is ``ON DELETE SET NULL``.** Losing the source chunk must not
  delete a concept that a roadmap already references; a concept whose
  provenance is gone is flagged unverifiable instead of disappearing (§3.1).
* **``verified`` is a human assertion, ``confidence`` is computed.** The
  extraction pipeline never sets ``verified``; only a reviewer does, which is
  why the upsert refuses to modify a verified row (§6.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from coursellm.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from coursellm.graph.schemas import EdgeRelation


class ExtractionRunStatus(StrEnum):
    """Lifecycle of one extraction run.

    ``partial`` is a first-class outcome: a document whose chunks partly failed
    validation still contributes the concepts that did pass, and saying so is
    more useful than either discarding the run or pretending it succeeded.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class AliasSource(StrEnum):
    """Who introduced an alias. A human alias is never overwritten by extraction."""

    EXTRACTION = "extraction"
    HUMAN = "human"


class Concept(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A named thing a student can learn, with the provenance that introduced it."""

    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "course_id", "slug", name="uq_concepts_tenant_course_slug"),
        Index("idx_concepts_tenant_course", "tenant_id", "course_id"),
        Index(
            "idx_concepts_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_shape"),
        CheckConstraint("char_length(name) BETWEEN 2 AND 200", name="name_len"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit"),
    )

    # Nullable: a cross-course concept ("gradient descent") is legitimate, and a
    # concept extracted before its course is known must not be rejected.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")

    # Provenance. ``SET NULL`` rather than ``CASCADE``: see the module docstring.
    provenance_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    provenance_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    provenance_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("graph_extraction_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # ``confidence`` is the strongest evidence that introduced the concept; it is
    # computed, never human-set, and ``verified`` is the separate human axis.
    confidence: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=0.0, server_default="0.000"
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    def __repr__(self) -> str:
        return f"<Concept {self.slug!r}>"


class ConceptAlias(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """An alternative surface form that resolves to one concept.

    ``course_id`` is denormalised from the parent concept on purpose: it scopes
    resolution to a course (so "attention" in a vision course does not resolve to
    the NLP concept) and it is part of the uniqueness constraint. A database
    trigger keeps it in sync, so application code cannot forget.
    """

    __tablename__ = "concept_aliases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "course_id", "alias_norm", name="concept_aliases_norm_unique"
        ),
        Index("idx_concept_aliases_lookup", "tenant_id", "course_id", "alias_norm"),
        CheckConstraint("source IN ('extraction', 'human')", name="source_known"),
    )

    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_norm: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[AliasSource] = mapped_column(
        pg_enum(AliasSource),
        nullable=False,
        default=AliasSource.EXTRACTION,
        server_default=AliasSource.EXTRACTION.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ConceptAlias {self.alias!r} -> {self.concept_id}>"


class ConceptEdge(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A directed, typed, evidenced relationship between two concepts.

    The unique key ``(tenant_id, source, target, relation)`` makes extraction
    idempotent: re-running over the same document merges evidence into one row
    instead of duplicating the edge. The self-loop check is the first of the
    five cycle-prevention measures in §3.7; the read-time path guard is the last.
    """

    __tablename__ = "concept_edges"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_concept_id",
            "target_concept_id",
            "relation",
            name="concept_edges_unique",
        ),
        CheckConstraint("source_concept_id <> target_concept_id", name="no_self_loop"),
        CheckConstraint("weight >= 0 AND weight <= 1", name="weight_unit"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit"),
        CheckConstraint("corroboration_count >= 1", name="corroboration_positive"),
        CheckConstraint("cue IN ('explicit', 'inferred')", name="cue_known"),
        Index("idx_concept_edges_out", "tenant_id", "source_concept_id", "relation"),
        Index("idx_concept_edges_in", "tenant_id", "target_concept_id", "relation"),
        Index(
            "idx_concept_edges_traversable",
            "tenant_id",
            "source_concept_id",
            postgresql_where=text("relation IN ('requires', 'part_of') AND confidence >= 0.6"),
        ),
        Index("idx_concept_edges_provenance", "provenance_document_id", "provenance_chunk_id"),
        Index(
            "idx_concept_edges_review",
            "tenant_id",
            text("created_at DESC"),
            postgresql_where=text("verified = false AND confidence < 0.75"),
        ),
    )

    source_concept_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_concept_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[EdgeRelation] = mapped_column(pg_enum(EdgeRelation), nullable=False)
    weight: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=1.0, server_default="1.000"
    )
    confidence: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=0.0, server_default="0.000"
    )
    corroboration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    cue: Mapped[str] = mapped_column(
        String(20), nullable=False, default="inferred", server_default="inferred"
    )

    provenance_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    provenance_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    provenance_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Every distinct provenance seen for this edge, unioned on re-extraction.
    # The scalar provenance columns keep the *first* source so the audit trail
    # is stable; this list is what proves later corroboration (§6.3).
    provenance_sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("graph_extraction_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)

    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    def __repr__(self) -> str:
        return f"<ConceptEdge {self.source_concept_id} -{self.relation}-> {self.target_concept_id}>"


class GraphExtractionRun(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """The audit ledger for one extraction over one document.

    One row per run makes a bad prompt revision revertible as a unit and makes
    extraction quality measurable: ``rejection_counts`` holds a per-gate tally so
    an operator can see *why* edges were rejected rather than only how many.
    """

    __tablename__ = "graph_extraction_runs"
    __table_args__ = (
        Index("idx_graph_extraction_runs_document", "tenant_id", "document_id"),
        Index("idx_graph_extraction_runs_started", "tenant_id", "started_at"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'partial')", name="status_known"
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    extraction_config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ExtractionRunStatus] = mapped_column(
        pg_enum(ExtractionRunStatus),
        nullable=False,
        default=ExtractionRunStatus.RUNNING,
        server_default=ExtractionRunStatus.RUNNING.value,
    )
    chunks_considered: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    concepts_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    edges_written: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    edges_rejected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    edges_queued_for_review: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Per-gate rejection tally, e.g. {"verbatim_provenance": 3, "cycle": 1}.
    rejection_counts: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<GraphExtractionRun doc={self.document_id} {self.status}>"


__all__ = [
    "AliasSource",
    "Concept",
    "ConceptAlias",
    "ConceptEdge",
    "ExtractionRunStatus",
    "GraphExtractionRun",
]
