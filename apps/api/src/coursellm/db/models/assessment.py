"""The persisted quiz draft.

A generated quiz must survive the request that produced it. ``GET
/api/v1/quizzes/{id}`` serves a previously generated draft, and the answer
endpoint needs the item (its rubric, its expected answer and its citations) in
order to score a submission, so the draft is stored rather than regenerated.

Two decisions are deliberate.

**The whole draft is one JSONB column, not ``quiz_items`` rows.** An item is
only ever read as part of its draft, is never queried independently, and has no
identity outside that draft. A child table would add a join and an ownership
question (which tenant does the item belong to?) without buying a query the
system performs.

**There is no foreign key to ``quiz_attempts``.** Attempts already carry a bare
``quiz_id`` so that a grade survives the deletion of the draft that produced it
(see :class:`~coursellm.db.models.learning.QuizAttempt`). The reverse direction
therefore cannot be a foreign key either without making the draft undeletable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from coursellm.db.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin


class Quiz(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One generated quiz draft, tenant- and user-scoped.

    ``items`` holds the serialised :class:`~coursellm.assessment.schemas.QuizItem`
    list; ``citations`` holds the resolved citation records so a reader does not
    need the retrieval context to render them. ``shortfall`` records items that
    were requested but could not be grounded, which is a fact the caller must be
    able to see rather than infer from a short list.
    """

    __tablename__ = "quizzes"
    __table_args__ = (
        Index("ix_quizzes_tenant_user_created", "tenant_id", "user_id", "created_at"),
        CheckConstraint("n_items >= 0", name="n_items_non_negative"),
        CheckConstraint("shortfall >= 0", name="shortfall_non_negative"),
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="difficulty_known",
        ),
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
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default="medium"
    )
    n_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    shortfall: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Concept ids the caller scoped the quiz to, as strings so the JSONB value is
    # stable across a psycopg/asyncpg round trip.
    concept_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    item_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    degraded: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Quiz {self.id} items={len(self.items or [])} course={self.course_id}>"


__all__ = ["Quiz"]
