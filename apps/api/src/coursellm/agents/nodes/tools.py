"""``tools`` — the one place a declared tool call becomes an execution.

Agent nodes declare; this node executes. Every permission check, timeout, retry
and audit record therefore lives in exactly one place
(:class:`~coursellm.tools.registry.ToolExecutor`), and the node's only job is to
map typed tool results back onto the state channels they belong to.

Failure policy: a tool that times out, errors, is denied or is unknown is
recorded, marked degraded where it changes what the student sees, and skipped.
The remaining calls still run and the turn still reaches ``answer_composer``.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import ToolMessage

from coursellm.agents.nodes import NodeFn
from coursellm.agents.state import (
    AgentError,
    ConversationState,
    DegradationReason,
    StudentProgress,
    ToolCallRecord,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.tools.planning import RoadmapPlan
from coursellm.tools.progress import StudentProgressResult
from coursellm.tools.quiz import AssessmentResult, QuizDraft
from coursellm.tools.recommend import RecommendationsResult
from coursellm.tools.registry import ToolExecutor

TOOLS_NODE = "tools"


def make_tools_node(*, settings: Settings, executor: ToolExecutor) -> NodeFn:
    """Build the node bound to a configured executor."""

    async def tools(state: ConversationState) -> dict[str, Any]:
        pending = state.get("pending_tool_calls") or []
        agent = state.get("intent", "tutor")
        remaining = max(settings.agent_max_tool_calls_per_turn - state.get("tool_call_count", 0), 0)
        executable = pending[:remaining]
        dropped = pending[remaining:]

        records: list[ToolCallRecord] = []
        errors: list[AgentError] = []
        degraded: list[DegradationReason] = []
        messages: list[Any] = []
        updates: dict[str, Any] = {
            "student_progress": state.get("student_progress"),
            "recommendations": state.get("recommendations") or [],
            "roadmap": state.get("roadmap"),
            "quiz": state.get("quiz"),
            "assessment": state.get("assessment"),
        }

        for call in executable:
            name = str(call.get("name", ""))
            arguments = call.get("arguments")
            before = len(executor.records)
            outcome = await executor.call(
                agent=str(agent),
                tool=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                state=state,
            )
            records.extend(executor.records[before:])
            _apply_result(updates, outcome.result)
            message_text = _summary(name, outcome.record["status"], outcome.result)
            messages.append(
                ToolMessage(
                    content=message_text,
                    tool_call_id=str(uuid.uuid4()),
                    name=name or "unknown_tool",
                )
            )
            if outcome.record["status"] == "timeout":
                degraded.append(DegradationReason.TOOL_TIMEOUT)
            elif outcome.record["status"] == "error":
                degraded.append(DegradationReason.TOOL_ERROR)
            if outcome.record["status"] != "ok":
                errors.append(
                    AgentError(
                        node=TOOLS_NODE,
                        error_type=outcome.record["status"],
                        message=(outcome.record["error"] or "")[:500],
                        retryable=outcome.record["status"] == "timeout",
                    )
                )

        for call in dropped:
            dropped_arguments = call.get("arguments")
            records.append(
                {
                    "tool": str(call.get("name", "")),
                    "arguments": (dropped_arguments if isinstance(dropped_arguments, dict) else {}),
                    "status": "error",
                    "latency_ms": 0,
                    "error": (
                        "ToolLimitReached: AGENT_MAX_TOOL_CALLS_PER_TURN would be "
                        "exceeded, so the call was dropped."
                    ),
                }
            )
            errors.append(
                AgentError(
                    node=TOOLS_NODE,
                    error_type="tool_limit_reached",
                    message=("The per-turn tool-call ceiling was reached before this call ran."),
                    retryable=False,
                )
            )
        if dropped:
            degraded.append(DegradationReason.TOOL_LIMIT_REACHED)

        update = {
            **updates,
            "tool_calls": records,
            "tool_call_count": state.get("tool_call_count", 0) + len(executable),
            "errors": errors,
            "messages": messages,
            # Cleared: the calls have been executed (or dropped) and a stale
            # pending list would route the graph straight back into this node.
            "pending_tool_calls": [],
            "degraded": degraded,
        }
        return validate_update(update)

    return tools


def _apply_result(updates: dict[str, Any], result: Any) -> None:
    """Map a typed tool result onto the state channel that owns it."""
    if result is None:
        return
    if isinstance(result, StudentProgressResult):
        progress = StudentProgress(
            course_id=result.course_id,
            mastery=dict(result.mastery),
            attempts=dict(result.attempts),
            last_seen=dict(result.last_seen),
            weak_concepts=list(result.weak_concepts),
            completed_steps=list(result.completed_steps),
        )
        updates["student_progress"] = progress
    elif isinstance(result, QuizDraft):
        updates["quiz"] = result.model_dump()
    elif isinstance(result, AssessmentResult):
        updates["assessment"] = result.model_dump()
    elif isinstance(result, RoadmapPlan):
        updates["roadmap"] = result.model_dump()
    elif isinstance(result, RecommendationsResult):
        updates["recommendations"] = [item.model_dump() for item in result.recommendations]


def _summary(name: str, status: str, result: Any) -> str:
    if status != "ok":
        return f"The {name or 'requested'} tool did not complete: {status}."
    if result is None:
        return f"The {name} tool returned no result."
    if isinstance(result, list):
        return f"The {name} tool returned {len(result)} item(s)."
    return f"The {name} tool completed successfully."


__all__ = ["TOOLS_NODE", "make_tools_node"]
