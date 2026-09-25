"""Request and response schemas for chat.

**What is deliberately absent.** Prompts, the assembled evidence region and the
provider model name are not part of any response: they are internal, they can
contain other students' material by proxy, and publishing the model name would
invite provider-specific requests the router does not honour. Usage is summarised
as token counts only, which is what a cost/limit display needs.

**Citations carry a bounded quote.** A citation a reader cannot check is a weak
citation, so each one includes a short, sentence-trimmed span of the passage it
points at. It is bounded here at the transport edge; the requester owns the
documents, so exposing their own passage back to them is not a disclosure.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coursellm.db.models.conversation import MessageRole
from coursellm.rag.generation.citations import bounded_quote
from coursellm.tools.registry import ProposedAction

# The service enforces ``settings.max_query_chars``; this bound is a transport
# guard so that a pathological body is rejected before it reaches the domain.
_MAX_QUESTION_CHARS = 100_000

#: ``agent`` runs the bounded LangGraph tutor (the default); ``rag`` runs the
#: direct retrieval-augmented path. Both share retrieval, generation, citations
#: and persistence, so the choice changes routing, not grounding.
Engine = Literal["agent", "rag"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=_MAX_QUESTION_CHARS)
    course_id: uuid.UUID | None = Field(
        default=None, description="Restrict retrieval to one of the caller's courses."
    )
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue an existing conversation owned by the caller."
    )
    engine: Engine = Field(
        default="agent",
        description=(
            "Which engine answers the turn. 'agent' (default) runs the bounded "
            "LangGraph tutor and records the routed intent; 'rag' runs the direct "
            "retrieval-augmented path. Both produce equivalent grounded answers."
        ),
    )


class CitationResponse(BaseModel):
    """A resolvable citation, with the passage span it points at."""

    model_config = ConfigDict(from_attributes=True)

    citation_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None
    source_type: str
    #: A short span of the cited passage, so the reader can check the answer
    #: without opening the document. Empty when the source carried no text.
    quote: str = ""

    @field_validator("quote")
    @classmethod
    def _bound_quote(cls, value: str) -> str:
        """Apply the transport bound to every citation, whatever produced it.

        The stored span is longer (assembly writes up to 280 chars); bounding at
        the schema means the chat response and `GET /chat/conversations/{id}`
        return the same shape for both engines.
        """
        return bounded_quote(value)


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
    #: The intent the router recorded for the turn. The direct path always
    #: reports ``"tutor"`` because it has no routing decision to make.
    intent: str = "tutor"
    #: The active trace id, so a stored answer can be joined to the trace that
    #: produced it. ``None`` when tracing is disabled.
    trace_id: str | None = None
    #: Consequential writes the agent proposed but did not execute. The client
    #: confirms one by returning its signed ``token`` to ``POST /chat/confirm``;
    #: the model never holds an execution capability for these actions.
    proposed_actions: list[ProposedAction] = Field(default_factory=list)


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
    "Engine",
    "MessageResponse",
    "UsageSummary",
]
