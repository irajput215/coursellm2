"""Conversation models.

Conversation history is persisted relationally rather than only in the agent
checkpointer. The checkpointer makes a run resumable; this table makes a
conversation *readable* — listable, searchable, exportable and deletable — which
is what the product and a data-deletion request both require.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Conversation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A thread of tutoring turns."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_user_updated", "tenant_id", "user_id", "updated_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: a student may ask a cross-course question ("what should I revise
    # first?"), and forcing a course would either block that or invent one.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New conversation")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.id}>"


class Message(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A single turn.

    ``citations`` is stored on the message rather than recomputed on read. A
    citation is a claim about which evidence supported an answer at the time it
    was generated; re-deriving it later from a changed corpus would silently
    rewrite history.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "ix_messages_tenant_conversation_created",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        pg_enum(MessageRole),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Citation records: chunk id, document id, page, source type, quoted span.
    # Typed as JSONB rather than a child table because they are always read and
    # written as a unit with the message and never queried by their own fields.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Reasons generation was degraded for this turn, e.g. ["reranker_unavailable"].
    # Persisted so that a user-visible "this answer was produced without the
    # reranker" notice is auditable after the fact.
    degraded: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    grounded: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Retrieval configuration in force when this message was produced. Without
    # it, comparing two answers gives no way to tell whether the retrieval
    # pipeline changed.
    retrieval_config_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- Observability columns (docs/architecture/observability.md section 10.1) ---
    # Nullable because every row written before this revision predates them, and
    # because a non-agent chat turn has no routed intent to record. They join a
    # stored answer to the trace that produced it and make a latency regression
    # answerable from the transcript alone.
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")

    def __repr__(self) -> str:
        return f"<Message {self.role} conv={self.conversation_id}>"
