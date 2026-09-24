"""``student_context`` — deterministic course and progress resolution.

No model is called here. The node resolves what the turn is *about* — the scoped
course, the student's progress projection, the rolling summary — so that
downstream nodes do not each re-read the same rows or re-parse the question.

A progress read that fails is degradation, not failure: the answer proceeds
unpersonalised and the banner says why. Personalisation must be *read* data, and a
read that did not happen must not be replaced with invented mastery.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from coursellm.agents.nodes import NodeFn
from coursellm.agents.state import (
    ConversationState,
    DegradationReason,
    StudentProgress,
    empty_progress,
    merge_evaluation_metadata,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger

logger = get_logger(__name__)

STUDENT_CONTEXT_NODE = "student_context"

ProgressProvider = Callable[[ConversationState], Awaitable[StudentProgress]]


def make_student_context_node(
    *,
    settings: Settings,
    progress_provider: ProgressProvider | None = None,
) -> NodeFn:
    """Build the node bound to an optional progress reader."""

    async def student_context(state: ConversationState) -> dict[str, Any]:
        course_id = state.get("current_course_id")
        degraded: list[DegradationReason] = []
        if progress_provider is None:
            progress = empty_progress(course_id)
        else:
            try:
                progress = await progress_provider(state)
            except Exception as exc:
                logger.warning(
                    "student_context_progress_unavailable", error_type=type(exc).__name__
                )
                progress = empty_progress(course_id)
                degraded.append(DegradationReason.PROGRESS_UNAVAILABLE)

        resolved_course = course_id
        if resolved_course is None and progress.get("course_id"):
            try:
                resolved_course = uuid.UUID(progress["course_id"])
            except ValueError:
                resolved_course = None

        metadata = merge_evaluation_metadata(
            state,
            grounded=False,
            models={
                **(_metadata(state).get("models") or {}),
                "agent_router": settings.resolved_agent_router_model,
                "agent": settings.resolved_agent_model,
            },
        )
        update = {
            "student_progress": progress,
            "current_course_id": resolved_course,
            "conversation_summary": state.get("conversation_summary"),
            "evaluation_metadata": metadata,
            "degraded": degraded,
        }
        return validate_update(update)

    return student_context


def _metadata(state: ConversationState) -> dict[str, Any]:
    value = state.get("evaluation_metadata")
    return dict(value) if value else {}


__all__ = ["STUDENT_CONTEXT_NODE", "ProgressProvider", "make_student_context_node"]
