"""Learning models: roadmaps, their ordered steps, and the progress record.

Three decisions carry this module.

**Progress is append-only.** ``progress_events`` and ``quiz_attempts`` are an
event log, not a mutable gradebook. Mastery is a *projection* over that log
(:mod:`coursellm.learning.progress`), never a column that is overwritten. That is
what makes "why does the system think I know this?" answerable after the fact, and
it is why a regrade is a new row rather than an edit.

**A roadmap is versioned, not mutated.** ``roadmaps.revision`` starts at 1 and a
change of plan writes a *new* row; the previous one is marked ``superseded``
rather than deleted. ``roadmap_steps`` stores the computed order and the blocking
prerequisites, so presenting a plan never re-derives it at read time and a diff
between two revisions is meaningful.

**Effort is stored, not invented at read time.** ``estimated_hours`` is computed
once by :mod:`coursellm.learning.planner` from concept difficulty and prerequisite
fan-in and then persisted, so the number a student sees is the number that was
planned and cannot drift between requests.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    UUIDPrimaryKeyMixin,
    pg_enum,
)


class ProgressEventKind(StrEnum):
    """What produced a progress observation.

    The kind is descriptive, not authoritative: the ``mastery`` value on the
    event is the measurement. Keeping both means the projection can be recomputed
    and the provenance of any number can still be explained.
    """

    QUIZ_ATTEMPT = "quiz_attempt"
    TOPIC_COMPLETED = "topic_completed"
    CONCEPT_MASTERED = "concept_mastered"
    CONCEPT_STRUGGLED = "concept_struggled"
    ROADMAP_STEP_COMPLETED = "roadmap_step_completed"


class RoadmapStatus(StrEnum):
    """Lifecycle of a roadmap revision.

    ``superseded`` is the audit-preserving alternative to deletion: a student's
    history of plans stays readable when the plan changes.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"


class RoadmapStepStatus(StrEnum):
    """Where one ordered step is in the student's progress."""

    PENDING = "pending"
    AVAILABLE = "available"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ProgressEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One append-only observation about a student's mastery.

    There is deliberately no ``updated_at`` and no update path: this table is the
    source of truth, and rewriting history would make the projection
    un-auditable. ``metadata`` is exposed as ``event_metadata`` in Python because
    ``metadata`` is reserved by the declarative API.
    """

    __tablename__ = "progress_events"
    __table_args__ = (
        Index(
            "ix_progress_events_tenant_user_occurred",
            "tenant_id",
            "user_id",
            "occurred_at",
        ),
        Index(
            "ix_progress_events_tenant_user_concept",
            "tenant_id",
            "user_id",
            "concept_id",
        ),
        CheckConstraint("mastery >= 0 AND mastery <= 1", name="mastery_unit"),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        CheckConstraint(
            "kind IN ('quiz_attempt', 'topic_completed', 'concept_mastered', "
            "'concept_struggled', 'roadmap_step_completed')",
            name="kind_known",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: a cross-course observation ("geometry clicked") is legitimate.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    concept_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind: Mapped[ProgressEventKind] = mapped_column(
        pg_enum(ProgressEventKind, length=32),
        nullable=False,
        default=ProgressEventKind.TOPIC_COMPLETED,
        server_default=ProgressEventKind.TOPIC_COMPLETED.value,
    )
    # The measured mastery the event asserts, in [0, 1]. For a struggled event
    # this is low; for a completion it is high. The projection never guesses.
    mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    # How much this observation should count relative to others.
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # What produced the event ("quiz", "roadmap_step", "assessment", ...).
    source: Mapped[str] = mapped_column(
        String(80), nullable=False, default="unknown", server_default="unknown"
    )
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    def __repr__(self) -> str:
        return f"<ProgressEvent {self.kind} concept={self.concept_id} mastery={self.mastery}>"


class QuizAttempt(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One scored answer, kept as evidence rather than as a running average.

    ``quiz_id`` is a bare UUID with no foreign key: the ``quizzes`` table is PR
    13, and a grade must not be lost because the draft that produced it was
    deleted. ``concept_ids`` is denormalised so the progress projection does not
    need to join through the quiz item on every read.
    """

    __tablename__ = "quiz_attempts"
    __table_args__ = (
        Index("ix_quiz_attempts_tenant_user_created", "tenant_id", "user_id", "created_at"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_unit"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Intentionally not a foreign key; see the class docstring.
    quiz_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    concept_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    rubric: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    misconceptions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<QuizAttempt item={self.item_id} score={self.score}>"


class Roadmap(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One revision of one goal's plan.

    ``revision`` is per goal, not per row: revision 1 is the first plan for a
    goal, revision 2 the next, and so on. ``reason`` records *why* the revision
    exists, which is what makes the history readable rather than merely present.
    """

    __tablename__ = "roadmaps"
    __table_args__ = (
        Index("ix_roadmaps_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index("ix_roadmaps_tenant_user_goal", "tenant_id", "user_id", "goal_concept_id"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("char_length(goal_text) BETWEEN 1 AND 500", name="goal_text_len"),
        CheckConstraint("estimated_hours >= 0", name="estimated_hours_non_negative"),
        CheckConstraint("status IN ('active', 'superseded', 'completed')", name="status_known"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # ``SET NULL`` rather than ``CASCADE``: losing a concept must not delete the
    # plan a student is following; the step text survives on its own.
    goal_concept_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[RoadmapStatus] = mapped_column(
        pg_enum(RoadmapStatus),
        nullable=False,
        default=RoadmapStatus.ACTIVE,
        server_default=RoadmapStatus.ACTIVE.value,
    )
    estimated_hours: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Roadmap goal={self.goal_text!r} rev={self.revision} {self.status}>"


class RoadmapStep(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One ordered, persisted step of a roadmap revision.

    ``blocked_by`` holds the *concept* ids that must be mastered before this step
    can start. Concept ids rather than step ids because the blocking relationship
    is a fact about the curriculum, not about one revision's row ids; a client
    resolves them through the steps in the same response. The column is JSONB
    rather than a join table because a step has a handful of blockers at most and
    they are always read with the step.
    """

    __tablename__ = "roadmap_steps"
    __table_args__ = (
        UniqueConstraint("roadmap_id", "order_index", name="uq_roadmap_steps_roadmap_order"),
        Index("ix_roadmap_steps_tenant_roadmap", "tenant_id", "roadmap_id"),
        CheckConstraint("order_index >= 0", name="order_index_non_negative"),
        CheckConstraint("estimated_hours >= 0", name="estimated_hours_non_negative"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 300", name="title_len"),
        CheckConstraint(
            "status IN ('pending', 'available', 'in_progress', 'completed', 'blocked')",
            name="status_known",
        ),
    )

    roadmap_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    concept_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[RoadmapStepStatus] = mapped_column(
        pg_enum(RoadmapStepStatus),
        nullable=False,
        default=RoadmapStepStatus.PENDING,
        server_default=RoadmapStepStatus.PENDING.value,
    )
    blocked_by: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    estimated_hours: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<RoadmapStep #{self.order_index} {self.title!r} {self.status}>"


__all__ = [
    "ProgressEvent",
    "ProgressEventKind",
    "QuizAttempt",
    "Roadmap",
    "RoadmapStatus",
    "RoadmapStep",
    "RoadmapStepStatus",
]
