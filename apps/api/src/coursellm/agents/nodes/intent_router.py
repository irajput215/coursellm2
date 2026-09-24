"""``intent_router`` — resolve *what is being asked* once, cheaply.

The node calls a small model once with a strict structured-output schema. If the
call fails, the schema does not validate, or the model's confidence is below
``INTENT_MIN_CONFIDENCE``, it falls back to deterministic keyword rules and
ultimately to ``tutor``. The turn never fails here: a routing miss costs a worse
answer, while an exception costs the student the turn.

The conditional edge out of this node's successor is
:func:`coursellm.agents.routing.route_intent`, which is pure and does no I/O.
"""

from __future__ import annotations

import time
from typing import Any, cast

from langchain_core.messages import HumanMessage

from coursellm.agents import budgets
from coursellm.agents.nodes import NodeFn
from coursellm.agents.routing import IntentClassification
from coursellm.agents.state import (
    AgentDecision,
    ConversationState,
    Intent,
    add_usage,
    message_history,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.llm import ChatMessage, LLMGateway, LLMRequest, ModelTask

logger = get_logger(__name__)

INTENT_ROUTER_NODE = "intent_router"

_SYSTEM = (
    "You classify a student's message for a course tutoring system. Choose exactly "
    "one intent:\n"
    "- tutor: a grounded question about course material, an explanation, or a "
    "prerequisite question.\n"
    "- planner: build or revise a study roadmap for a goal.\n"
    "- recommender: map a knowledge gap to learning resources.\n"
    "- assessment: generate a quiz or evaluate a submitted answer.\n"
    "- progress: summarise mastery, activity or weak concepts.\n"
    "Return the intent, a confidence in [0, 1], and, when the message names one, "
    "a course id and topic. Do not answer the question."
)

# Ordered longest-signal-first so a specific phrase beats a generic one.
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "planner",
        (
            "study plan",
            "learning plan",
            "roadmap",
            "learning path",
            "revision plan",
            "plan my",
            "what should i study",
            "schedule for",
        ),
    ),
    (
        "assessment",
        (
            "quiz me",
            "quiz",
            "test me",
            "practice question",
            "practice questions",
            "exam question",
            "grade my",
            "evaluate my answer",
            "mark my",
            "assess my",
        ),
    ),
    (
        "recommender",
        (
            "recommend",
            "resources",
            "resource",
            "where can i learn",
            "what should i read",
            "book to read",
            "video",
            "course to take",
        ),
    ),
    (
        "progress",
        (
            "how am i doing",
            "my progress",
            "how much have i",
            "weak concept",
            "weak concepts",
            "mastery",
            "what should i revise",
            "where am i struggling",
        ),
    ),
)


def classify_pattern(question: str) -> Intent:
    """Cheap deterministic rules, used when the model is unavailable or unsure."""
    lowered = question.lower()
    for intent, phrases in _PATTERNS:
        if any(phrase in lowered for phrase in phrases):
            return cast(Intent, intent)
    return "tutor"


def _last_question(state: ConversationState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
    return ""


def make_intent_router_node(
    *,
    settings: Settings,
    gateway: LLMGateway,
) -> NodeFn:
    """Build the router node bound to ``settings`` and ``gateway``."""

    async def intent_router(state: ConversationState) -> dict[str, Any]:
        started = time.perf_counter()
        question = _last_question(state)
        intent: Intent = "tutor"
        decision = "router_fallback"
        rationale = "No routable classification was produced."
        model: str | None = None
        course_id = state.get("current_course_id")
        usage = state.get("token_usage")

        breach = budgets.model_budget_breach(state, settings)
        if breach is not None:
            intent = classify_pattern(question)
            decision = f"pattern_rules:{breach.value}"
            rationale = "Model budget exhausted before routing; used deterministic rules."
        else:
            request = LLMRequest(
                task=ModelTask.CLASSIFICATION,
                messages=[
                    ChatMessage(role="system", content=_SYSTEM),
                    *message_history(state, limit=4),
                    ChatMessage(role="user", content=question or "(empty message)"),
                ],
                temperature=0.0,
                response_model=IntentClassification,
                purpose="agent.intent_router",
            )
            try:
                response = await gateway.complete(request)
            except Exception as exc:  # the router never fails the turn
                logger.warning("intent_router_degraded", error_type=type(exc).__name__)
                intent = classify_pattern(question)
                decision = "pattern_rules"
                rationale = f"Model call failed ({type(exc).__name__}); used deterministic rules."
            else:
                usage = add_usage(
                    usage,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                    cost_usd=response.cost_usd,
                )
                model = response.model
                classification = (
                    response.parsed if isinstance(response.parsed, IntentClassification) else None
                )
                if classification is None:
                    intent = classify_pattern(question)
                    decision = "pattern_rules"
                    rationale = "The response did not satisfy IntentClassification."
                elif classification.confidence < settings.intent_min_confidence:
                    intent = classify_pattern(question)
                    decision = "router_fallback"
                    rationale = (
                        f"Model confidence {classification.confidence:.2f} is below "
                        f"INTENT_MIN_CONFIDENCE={settings.intent_min_confidence}; "
                        "used deterministic rules."
                    )
                else:
                    intent = classification.intent
                    decision = intent
                    rationale = classification.reason or "Structured classification accepted."
                    if classification.course_id is not None:
                        # Recorded as a hint only. plan_retrieval drops a course id
                        # that is not the turn's scoped course, so a model cannot
                        # widen retrieval to a course it was not given.
                        course_id = classification.course_id

        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        entry = AgentDecision(
            node=INTENT_ROUTER_NODE,
            decision=decision,
            rationale=rationale[:500],
            model=model,
            prompt_version=None,
            latency_ms=latency_ms,
        )
        update = {
            "intent": intent,
            "current_course_id": course_id,
            "current_topic": state.get("current_topic"),
            "agent_decisions": [entry],
            "token_usage": usage,
        }
        return validate_update(update)

    return intent_router


__all__ = ["INTENT_ROUTER_NODE", "classify_pattern", "make_intent_router_node"]
