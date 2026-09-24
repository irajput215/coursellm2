"""Request and response schemas for an agent turn.

Separate from the chat schemas because an agent turn returns more than prose: the
routed intent, the audit trail (decisions and tool calls), and whichever typed
artifact the capability produced. Citations are narrower than the chat schema's:
an agent citation is the identifier a student needs to open the source, and the
quoted span and provider model name are deliberately absent for the same reasons
they are absent from the chat response.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from coursellm.services.agent import AgentTurnResult

_MAX_QUESTION_CHARS = 100_000


class AgentTurnRequest(BaseModel):
    """The transport shape of an agent turn."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=_MAX_QUESTION_CHARS)
    course_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None


class AgentCitationResponse(BaseModel):
    """A resolvable citation. The quoted evidence is intentionally not exposed."""

    model_config = ConfigDict(from_attributes=True)

    citation_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    page: int | None = None
    source_type: str


class AgentDecisionResponse(BaseModel):
    """One entry of the append-only routing and tool-decision audit."""

    node: str
    decision: str
    rationale: str
    model: str | None = None
    prompt_version: str | None = None
    latency_ms: int


class ToolCallResponse(BaseModel):
    """One tool attempt, with redacted arguments and its terminal status."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    latency_ms: int
    error: str | None = None


class AgentUsageSummary(BaseModel):
    """Token accounting across every model call in the turn, without model names."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    calls: int


class AgentTurnResponse(BaseModel):
    """A completed agent turn, including its typed artifacts."""

    conversation_id: uuid.UUID
    intent: str
    answer: str
    citations: list[AgentCitationResponse]
    grounded: bool
    degraded: list[str]
    roadmap: dict[str, Any] | None = None
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    quiz: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    decisions: list[AgentDecisionResponse] = Field(default_factory=list)
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)
    usage: AgentUsageSummary | None = None

    @classmethod
    def from_result(cls, result: AgentTurnResult) -> AgentTurnResponse:
        """Adapt the service's typed result to the response model."""
        return cls(
            conversation_id=result.conversation_id,
            intent=result.intent,
            answer=result.answer,
            citations=[
                AgentCitationResponse.model_validate(citation) for citation in result.citations
            ],
            grounded=result.grounded,
            degraded=result.degraded,
            roadmap=result.roadmap,
            recommendations=result.recommendations,
            quiz=result.quiz,
            assessment=result.assessment,
            decisions=[
                AgentDecisionResponse.model_validate(decision) for decision in result.decisions
            ],
            tool_calls=[ToolCallResponse.model_validate(record) for record in result.tool_calls],
            usage=AgentUsageSummary.model_validate(result.token_usage),
        )


__all__ = [
    "AgentCitationResponse",
    "AgentDecisionResponse",
    "AgentTurnRequest",
    "AgentTurnResponse",
    "AgentUsageSummary",
    "ToolCallResponse",
]
