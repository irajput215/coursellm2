"""The conditional edges of the agent graph — pure functions over state.

Every routing decision in ``docs/architecture/agent-architecture.md`` section 3
lives here, and none of them performs I/O. That is what makes routing testable
over hundreds of cases in milliseconds and what makes the graph's control flow a
property of the code rather than of a prompt.

The three functions answer three different questions:

* :func:`route_intent` — *which capability handles this turn?* Unknown, absent
  or unclassified intent falls back to the generalist, which owns the refusal
  path. A wrong answer is recoverable; a wrong write is not.
* :func:`route_after_agent` — *may this turn spend anything else?* Bounds are
  checked before the spend, so a breach costs nothing further.
* :func:`route_after_evidence` — *is the evidence sufficient, and if not, which
  agent gets another pass?* Sufficiency is computed from typed evidence, never
  judged by the model that produced the answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from coursellm.agents import budgets
from coursellm.agents.state import INTENTS, ConversationState, Intent, RetrievedDocument
from coursellm.core.config import Settings

#: Tools whose pending calls are answered by the retrieval sub-path
#: (``plan_retrieval`` -> ``retrieval`` -> ``rerank`` -> ``knowledge_graph``)
#: rather than by the generic ``tools`` executor. ``search_course`` is excluded
#: because it returns course structure rather than passages.
RETRIEVAL_TOOLS: frozenset[str] = frozenset(
    {
        "search_documents",
        "search_books",
        "search_web_sources",
        "search_knowledge_graph",
    }
)

_ROUTABLE: frozenset[str] = frozenset(INTENTS)

AgentName = str

COMPOSE = "answer_composer"


def _settings_or_default(settings: Settings | None) -> Settings:
    if settings is not None:
        return settings
    from coursellm.core import config as config_module

    return config_module.settings


class IntentClassification(BaseModel):
    """The structured output the router node asks for.

    ``extra="forbid"`` is deliberate: a provider that invents a field should be
    a validation failure that falls back to pattern rules, not a silently
    ignored key.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    course_id: UUID | None = None
    topic: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="", max_length=200)


def route_intent(state: ConversationState) -> Intent:
    """Conditional edge out of ``student_context``.

    Returns one of the five routable intents. Anything absent, unknown or not a
    string routes to ``tutor``.
    """
    intent = state.get("intent")
    if isinstance(intent, str) and intent in _ROUTABLE:
        return intent
    return "tutor"


def route_after_agent(state: ConversationState, settings: Settings | None = None) -> str:
    """Conditional edge out of every agent node and out of ``tools``.

    Returns ``"retrieval"`` (which the graph maps to ``plan_retrieval``),
    ``"tools"`` or ``"compose"``. Deterministic admission control: the model
    never decides whether a turn has exceeded its budget.
    """
    resolved = _settings_or_default(settings)
    if budgets.iteration_limit_reached(state, resolved):
        return "compose"
    if budgets.tool_limit_reached(state, resolved):
        return "compose"
    if budgets.past_deadline(state):
        return "compose"

    pending = state.get("pending_tool_calls") or []
    if not pending:
        return "compose"

    retrieval_calls = [call for call in pending if call.get("name") in RETRIEVAL_TOOLS]
    if retrieval_calls and not budgets.retrieval_limit_reached(state, resolved):
        return "retrieval"
    if pending:
        return "tools"
    return "compose"


def route_after_evidence(state: ConversationState, settings: Settings | None = None) -> str:
    """Conditional edge out of ``knowledge_graph``. Returns a node name.

    ``"answer_composer"`` is the only exit for a sufficient or exhausted turn;
    every other return value is an agent node selected from ``state["intent"]``.
    """
    resolved = _settings_or_default(settings)
    metadata = state.get("evaluation_metadata")
    grounded = bool(metadata.get("grounded", False)) if metadata else False
    pending = state.get("pending_tool_calls") or []

    if grounded and not pending:
        return COMPOSE
    if budgets.iteration_limit_reached(state, resolved):
        return COMPOSE
    if budgets.retrieval_limit_reached(state, resolved):
        return COMPOSE

    intent = state.get("intent")
    name = intent if isinstance(intent, str) and intent in _ROUTABLE else "tutor"
    return f"{name}_agent"


def intent_agent_node(intent: str) -> AgentName:
    """Map an intent to its graph node name."""
    name = intent if intent in _ROUTABLE else "tutor"
    return f"{name}_agent"


def assess_grounding(state: ConversationState, settings: Settings) -> dict[str, object]:
    """Compute the grounding verdict from the state's typed evidence.

    A passage counts as evidence when its cross-encoder score clears
    ``GROUNDING_MIN_RERANK_SCORE``, or when no score exists because reranking did
    not run: in that case the RRF order *is* the evidence, and treating it as
    absent would refuse a question the retriever answered.
    """
    return grounding_for(
        state.get("retrieved_documents") or [],
        bool(state.get("citations")),
        settings,
    )


def grounding_for(
    documents: Sequence[RetrievedDocument],
    resolvable_citation: bool,
    settings: Settings,
) -> dict[str, object]:
    """The grounding verdict for an explicit passage list.

    Exposed separately because the ``rerank`` node scores passages it has not yet
    written back into state, and needs the verdict for the merged view.
    """
    threshold = settings.grounding_min_rerank_score
    evidence = 0
    max_score: float | None = None
    for document in documents:
        score = document["rerank_score"]
        if score is None or score >= threshold:
            evidence += 1
        if score is not None and (max_score is None or score > max_score):
            max_score = score
    return {
        "grounded": evidence >= settings.grounding_min_evidence or resolvable_citation,
        "evidence_count": evidence,
        "max_rerank_score": max_score,
    }


__all__ = [
    "COMPOSE",
    "RETRIEVAL_TOOLS",
    "IntentClassification",
    "assess_grounding",
    "grounding_for",
    "intent_agent_node",
    "route_after_agent",
    "route_after_evidence",
    "route_intent",
]
