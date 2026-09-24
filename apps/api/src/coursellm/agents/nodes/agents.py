"""The five agent roles, built from one factory.

A role is a *configuration* — a system prompt, a declared tool allowlist, a
Pydantic decision schema and the state artifact it owns. The node that runs it is
the same shape for all five, which keeps the blast radius of a role change to the
role table below.

Two rules hold for every role:

* **The agent decides; it does not execute.** The only way a role reaches a tool
  is by returning ``tool_calls``, which the node writes to
  ``pending_tool_calls``. Permission checks, timeouts, retries and audit records
  happen in exactly one place, the executor.
* **A model failure degrades, it never raises and never fabricates.** The tutor
  defers to the composer's extractive fallback; the planner falls back to
  metadata ordering; the recommender returns an empty catalogue list; the
  assessment agent returns *no* quiz items rather than invented ones; the
  progress agent defers to a deterministic summary.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field

from coursellm.agents import budgets
from coursellm.agents.nodes import NodeFn
from coursellm.agents.state import (
    AgentDecision,
    AgentError,
    ConversationState,
    DegradationReason,
    Intent,
    add_usage,
    message_history,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.llm import ChatMessage, LLMGateway, LLMRequest, ModelTask
from coursellm.tools.permissions import AGENT_TOOLS

logger = get_logger(__name__)

Action = Literal["retrieve", "tool", "answer"]

#: Base keys every agent node writes, whatever its role.
_BASE_WRITES: frozenset[str] = frozenset(
    {
        "pending_tool_calls",
        "agent_decisions",
        "iteration_count",
        "messages",
        "token_usage",
        "degraded",
        "errors",
    }
)


class ToolCallRequest(BaseModel):
    """A tool the model asks for. It is a declaration, not an execution."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class TutorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action = "answer"
    tool_calls: list[ToolCallRequest] = Field(default_factory=list, max_length=6)
    answer: str | None = None
    rationale: str = Field(default="", max_length=500)


class PlannerStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    order: int = Field(default=0, ge=0)
    estimated_effort_minutes: int | None = Field(default=None, ge=0)
    completed: bool = False


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action = "answer"
    tool_calls: list[ToolCallRequest] = Field(default_factory=list, max_length=6)
    summary: str | None = None
    steps: list[PlannerStep] = Field(default_factory=list, max_length=50)
    rationale: str = Field(default="", max_length=500)


class RecommendationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    url: str | None = None
    source_type: str = "other"
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list, max_length=10)


class RecommenderDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action = "answer"
    tool_calls: list[ToolCallRequest] = Field(default_factory=list, max_length=6)
    summary: str | None = None
    items: list[RecommendationDraft] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=500)


class QuizItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)
    item_type: Literal["multiple_choice", "short_answer", "true_false"] = "multiple_choice"
    choices: list[str] = Field(default_factory=list, max_length=10)
    answer: str | None = None
    rubric: list[str] = Field(default_factory=list, max_length=10)
    citation_ids: list[str] = Field(default_factory=list, max_length=20)


class AssessmentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action = "answer"
    tool_calls: list[ToolCallRequest] = Field(default_factory=list, max_length=6)
    summary: str | None = None
    items: list[QuizItemDraft] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=500)


class ProgressDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action = "answer"
    tool_calls: list[ToolCallRequest] = Field(default_factory=list, max_length=6)
    summary: str | None = None
    weak_concepts: list[str] = Field(default_factory=list, max_length=50)
    next_actions: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(default="", max_length=500)


@dataclass(frozen=True, slots=True)
class AgentRole:
    """One role's configuration. Everything a role change touches lives here."""

    intent: Intent
    instructions: str
    decision_model: type[BaseModel]
    artifact: str | None
    writes: frozenset[str]

    @property
    def node_name(self) -> str:
        return f"{self.intent}_agent"


def _role(
    intent: Intent,
    instructions: str,
    decision_model: type[BaseModel],
    *artifact_keys: str,
) -> AgentRole:
    return AgentRole(
        intent=intent,
        instructions=instructions,
        decision_model=decision_model,
        artifact=artifact_keys[0] if artifact_keys else None,
        writes=_BASE_WRITES | frozenset(artifact_keys),
    )


_TUTOR = (
    "You are the tutor agent. Answer the student's question strictly from the "
    "retrieved evidence supplied in the conversation. If no evidence is present "
    "and you need it, return action='retrieve' with a search_documents call. If a "
    "tool would answer part of the question, return action='tool' with that call. "
    "When evidence is present, return action='answer' and a grounded answer that "
    "cites passages as [S1], [S2]. Never answer from memory and never invent a "
    "citation."
)

_PLANNER = (
    "You are the learning planner agent. Turn a goal into an ordered roadmap. "
    "Return action='retrieve' with a search_knowledge_graph or search_course call "
    "when you need prerequisite structure. When you have it, return "
    "action='answer' with an ordered list of steps. Ordering is computed from "
    "prerequisites, never narrated from memory. Do not invent concepts and do not "
    "delete completed steps."
)

_RECOMMENDER = (
    "You are the recommendation agent. Map the student's knowledge gaps to real "
    "catalogue resources. Return action='tool' with a get_recommendations call to "
    "rank the catalogue, or search_knowledge_graph to widen the concept set. Never "
    "invent a resource, a URL or a course."
)

_ASSESSMENT = (
    "You are the assessment agent. Generate quiz items grounded in cited "
    "passages, or evaluate a submitted answer against its rubric. Return "
    "action='retrieve' when you need passages, action='tool' to persist a quiz or "
    "record an evaluation, and action='answer' with items when drafting. Never "
    "invent a grade and never modify a previously recorded one."
)

_PROGRESS = (
    "You are the progress agent. Summarise mastery, activity and weak concepts "
    "from the student's recorded progress. Return action='tool' with a "
    "get_student_progress call when you need a refresh, or action='answer' with a "
    "summary. Do not alter grades and do not create or update a roadmap."
)

ROLES: dict[Intent, AgentRole] = {
    "tutor": _role("tutor", _TUTOR, TutorDecision, "answer_draft"),
    "planner": _role("planner", _PLANNER, PlannerDecision, "roadmap"),
    "recommender": _role("recommender", _RECOMMENDER, RecommenderDecision, "recommendations"),
    "assessment": _role("assessment", _ASSESSMENT, AssessmentDecision, "quiz", "assessment"),
    "progress": _role("progress", _PROGRESS, ProgressDecision),
}

#: Node name -> role, for the graph builder.
ROLE_BY_NODE: dict[str, AgentRole] = {role.node_name: role for role in ROLES.values()}


def _system_prompt(role: AgentRole, settings: Settings) -> str:
    tools = ", ".join(sorted(AGENT_TOOLS.get(role.intent, frozenset()))) or "none"
    return (
        f"{role.instructions}\n\n"
        f"Tools you may declare: {tools}. You do not execute tools; you only "
        f"declare them, and the system enforces permissions.\n"
        f"Answer temperature is {settings.agent_temperature}."
    )


def _context_message(state: ConversationState) -> str:
    lines = ["Current turn context:"]
    lines.append(f"- intent: {state.get('intent', 'tutor')}")
    if state.get("current_course_id"):
        lines.append(f"- course_id: {state['current_course_id']}")
    if state.get("learning_goal"):
        lines.append(f"- learning_goal: {state['learning_goal']}")
    if state.get("current_topic"):
        lines.append(f"- topic: {state['current_topic']}")
    progress: Mapping[str, Any] = state.get("student_progress") or {}
    mastery = progress.get("mastery") or {}
    if mastery:
        rendered = ", ".join(f"{key}={value:.2f}" for key, value in list(mastery.items())[:20])
        lines.append(f"- mastery: {rendered}")
    weak = progress.get("weak_concepts") or []
    if weak:
        lines.append(f"- weak_concepts: {', '.join(weak[:20])}")
    documents = state.get("retrieved_documents") or []
    if documents:
        lines.append(f"- evidence passages available: {len(documents)}")
        for document in documents[:5]:
            snippet = " ".join(document["content"].split())[:240]
            lines.append(f"  [{document['citation_id']}] {snippet}")
    entities = state.get("graph_entities") or []
    if entities:
        names = ", ".join(entity["name"] for entity in entities[:20])
        lines.append(f"- graph concepts: {names}")
    return "\n".join(lines)


def _fallback_update(
    role: AgentRole,
    state: ConversationState,
    *,
    error_type: str,
    message: str,
    decision: str,
    degraded: list[DegradationReason],
) -> dict[str, Any]:
    """The role's deterministic behaviour when the model is unavailable."""
    update: dict[str, Any] = {
        "pending_tool_calls": [],
        "agent_decisions": [
            AgentDecision(
                node=role.node_name,
                decision=decision,
                rationale=message[:500],
                model=None,
                prompt_version=None,
                latency_ms=0,
            )
        ],
        "iteration_count": state.get("iteration_count", 0) + 1,
        "messages": [AIMessage(content=f"[{role.node_name} degraded: {decision}]")],
        "token_usage": state.get("token_usage"),
        "degraded": degraded,
        "errors": [
            AgentError(
                node=role.node_name,
                error_type=error_type,
                message=message[:500],
                retryable=True,
            )
        ],
    }
    if role.intent == "planner":
        update["roadmap"] = _fallback_roadmap(state, message)
    elif role.intent == "recommender":
        update["recommendations"] = []
    elif role.intent == "assessment":
        # Never fabricate items: an empty quiz and an explicit error is the only
        # acceptable outcome when generation failed.
        update["quiz"] = None
        update["assessment"] = None
    elif role.intent == "tutor":
        update["answer_draft"] = None
    return update


def _fallback_roadmap(state: ConversationState, message: str) -> dict[str, Any]:
    """Metadata ordering from the graph entities already in state, or an empty plan."""
    entities = state.get("graph_entities") or []
    ordered = sorted(entities, key=lambda entity: (entity["depth"], entity["name"]))
    steps = [
        {
            "concept_id": entity["concept_id"],
            "title": entity["name"],
            "order": index,
            "completed": False,
        }
        for index, entity in enumerate(ordered)
    ]
    return {
        "steps": steps,
        "summary": message,
        "degraded": ["llm_unavailable"],
        "source": "metadata_ordering",
    }


def _artifact_update(role: AgentRole, parsed: BaseModel) -> dict[str, Any]:
    """Map a validated decision onto the role's state artifact."""
    if role.intent == "tutor":
        tutor = parsed if isinstance(parsed, TutorDecision) else None
        answer = tutor.answer if tutor is not None else None
        return {"answer_draft": ({"text": answer, "degraded": []} if answer else None)}
    if role.intent == "planner":
        planner = parsed if isinstance(parsed, PlannerDecision) else None
        if planner is None:
            return {"roadmap": None}
        return {
            "roadmap": {
                "summary": planner.summary or "",
                "steps": [step.model_dump() for step in planner.steps],
                "degraded": [],
            }
        }
    if role.intent == "recommender":
        recommender = parsed if isinstance(parsed, RecommenderDecision) else None
        items = recommender.items if recommender is not None else []
        return {"recommendations": [item.model_dump() for item in items]}
    if role.intent == "assessment":
        assessment = parsed if isinstance(parsed, AssessmentDecision) else None
        if assessment is None:
            return {"quiz": None, "assessment": None}
        return {
            "quiz": (
                {
                    "summary": assessment.summary or "",
                    "items": [item.model_dump() for item in assessment.items],
                }
                if assessment.items
                else None
            ),
            "assessment": {"summary": assessment.summary or ""},
        }
    return {}


def make_agent_node(
    role: AgentRole,
    *,
    settings: Settings,
    gateway: LLMGateway,
) -> NodeFn:
    """Build the node for ``role``."""

    async def agent(state: ConversationState) -> dict[str, Any]:
        started = time.perf_counter()
        breach = budgets.model_budget_breach(state, settings)
        if breach is not None:
            update = _fallback_update(
                role,
                state,
                error_type="BudgetExceeded",
                message=f"No model call was made: {breach.value}.",
                decision=f"budget_fallback:{breach.value}",
                degraded=[breach],
            )
            return validate_update(update)

        request = LLMRequest(
            task=ModelTask.REASONING,
            messages=[
                ChatMessage(role="system", content=_system_prompt(role, settings)),
                *message_history(state, limit=settings.history_window_messages),
                ChatMessage(role="user", content=_context_message(state)),
            ],
            temperature=settings.agent_temperature,
            response_model=role.decision_model,
            purpose=f"agent.{role.intent}",
        )
        try:
            response = await gateway.complete(request)
        except Exception as exc:
            logger.warning(
                "agent_node_degraded",
                intent=role.intent,
                error_type=type(exc).__name__,
            )
            update = _fallback_update(
                role,
                state,
                error_type=type(exc).__name__,
                message=f"The {role.intent} model call failed.",
                decision="llm_error",
                degraded=[DegradationReason.LLM_UNAVAILABLE],
            )
            return validate_update(update)

        parsed = response.parsed
        usage = add_usage(
            state.get("token_usage"),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            cost_usd=response.cost_usd,
        )
        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        pending: list[dict[str, Any]] = []
        decision_name: str = role.intent
        rationale = ""
        if isinstance(parsed, BaseModel):
            calls = getattr(parsed, "tool_calls", None)
            if isinstance(calls, list):
                pending = [
                    {"name": call.name, "arguments": dict(call.arguments)}
                    for call in calls
                    if isinstance(call, ToolCallRequest)
                ]
            action = getattr(parsed, "action", None)
            if action is not None:
                decision_name = str(action)
            rationale = str(getattr(parsed, "rationale", "") or "")

        main_update: dict[str, Any] = {
            "pending_tool_calls": pending,
            "agent_decisions": [
                AgentDecision(
                    node=role.node_name,
                    decision=decision_name,
                    rationale=rationale[:500],
                    model=response.model,
                    prompt_version=None,
                    latency_ms=latency_ms,
                )
            ],
            "iteration_count": state.get("iteration_count", 0) + 1,
            "messages": [AIMessage(content=response.text or f"[{role.node_name} {decision_name}]")],
            "token_usage": usage,
            "degraded": [],
            "errors": [],
        }
        if role.artifact is not None:
            main_update.update(
                _artifact_update(role, parsed) if isinstance(parsed, BaseModel) else {}
            )
        return validate_update(main_update)

    return agent


__all__ = [
    "ROLES",
    "ROLE_BY_NODE",
    "AgentRole",
    "AssessmentDecision",
    "PlannerDecision",
    "PlannerStep",
    "ProgressDecision",
    "QuizItemDraft",
    "RecommendationDraft",
    "RecommenderDecision",
    "ToolCallRequest",
    "TutorDecision",
    "make_agent_node",
]
