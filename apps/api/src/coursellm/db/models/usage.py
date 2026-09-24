"""Usage accounting model.

``llm_usage`` is the system of record for model spend (ADR-0007). One row is
written per provider attempt — including attempts that failed — so that cost,
latency and error rate are all answerable from the same table.

**There is deliberately no column holding prompt or completion text.** This is a
data-minimisation decision, not an oversight. The table exists to answer "how
much did this tenant spend, on which model, and how often did it fail"; student
document text and model answers would add nothing to that answer while turning
an operational table into a second copy of the corpus, subject to a different
retention and deletion path. The absence of the column is the control.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from coursellm.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from coursellm.llm.types import ModelTask


class LLMUsage(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One provider attempt, attributed to a tenant.

    ``user_id`` is nullable and ``ON DELETE SET NULL`` rather than ``CASCADE``:
    deleting a user must not erase the tenant's spend history. The same logic
    applies to ``conversation_id``, which is a bare UUID with no foreign key so
    that a conversation can be deleted without rewriting the accounting record.
    """

    __tablename__ = "llm_usage"
    __table_args__ = (
        # Time-series reads: "spend for this tenant over the last week".
        Index("ix_llm_usage_tenant_created_at", "tenant_id", "created_at"),
        # Per-model breakdown, e.g. fallback frequency and route share.
        Index("ix_llm_usage_tenant_model", "tenant_id", "model"),
        # Correlate every attempt made for one logical request.
        Index("ix_llm_usage_tenant_request", "tenant_id", "request_id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Intentionally not a foreign key; see the class docstring.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # Correlates attempts with the request trace that produced them.
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )

    task: Mapped[ModelTask] = mapped_column(pg_enum(ModelTask), nullable=False)
    # A short call-site label such as "tutor.answer". Never prompt text.
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    used_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0"), server_default="0"
    )
    # False when no price was known. Recorded so a spend total can be labelled
    # partial instead of silently understated.
    priced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 1-based position of this attempt within the logical request, across the
    # whole fallback chain, so a request's attempts can be replayed in order.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Exception class name for a failed attempt; NULL on success.
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)

    def __repr__(self) -> str:
        return f"<LLMUsage {self.model} task={self.task} attempt={self.attempt}>"
