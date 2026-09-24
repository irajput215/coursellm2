"""Request and response schemas for chat.

**What is deliberately absent.** Citations carry the identifiers a student needs
to open the source (filename, page, document id), but not the quoted evidence
text. Prompts, the assembled evidence region and the provider model name are not
part of any response either: they are internal, they can contain other students'
material by proxy, and publishing the model name would invite provider-specific
requests the router does not honour. Usage is summarised as token counts only,
which is what a cost/limit display needs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from coursellm.db.models.conversation import MessageRole

# The service enforces ``settings.max_query_chars``; this bound is a transport
# guard so that a pathological body is rejected before it reaches the domain.
_MAX_QUESTION_CHARS = 100_000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=_MAX_QUESTION_CHARS)
    course_id: uuid.UUID | None = Field(
        default=None, description="Restrict retrieval to one of the caller's courses."
    )
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue an existing conversation owned by the caller."
    )


class CitationResponse(BaseModel):
    """A resolvable citation. The quoted span is intentionally not exposed."""

    model_config = ConfigDict(from_attributes=True)

    citation_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None
    source_type: str


class UsageSummary(BaseModel):
    """Token accounting for one model call, without the provider model name."""

    model_config = ConfigDict(from_attributes=True)

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    grounded: bool
    degraded: list[str]
    conversation_id: uuid.UUID
    usage: UsageSummary | None = None


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    course_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    citations: list[CitationResponse]
    degraded: list[str]
    grounded: bool
    created_at: datetime


class ConversationDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    course_id: uuid.UUID | None
    created_at: datetime
    messages: list[MessageResponse]


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "CitationResponse",
    "ConversationDetailResponse",
    "ConversationSummary",
    "MessageResponse",
    "UsageSummary",
]
