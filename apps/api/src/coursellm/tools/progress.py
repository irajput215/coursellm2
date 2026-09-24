"""``get_student_progress`` — mastery, attempts and weak concepts.

The read projection is real: the handler returns whatever
``state["student_progress"]`` holds, optionally narrowed by course and concept,
which is data the ``student_context`` node loaded. The *write* side
(``progress_events``, ``quiz_attempts``) is PR 13, so until then the projection
is honestly empty rather than fabricated.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)


class GetStudentProgressArgs(BaseModel):
    """Arguments for the progress projection. Scope is injected, never passed."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID | None = None
    concept_ids: list[str] | None = Field(default=None, max_length=200)


class StudentProgressResult(BaseModel):
    """Mastery, activity and gaps for one student and course."""

    course_id: str | None = None
    mastery: dict[str, float] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)
    last_seen: dict[str, str] = Field(default_factory=dict)
    weak_concepts: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def get_student_progress(
    args: GetStudentProgressArgs, ctx: ToolContext
) -> StudentProgressResult:
    """Project the progress already in state, narrowed by the call's filters."""
    raw = _as_mapping(ctx.progress)
    concept_filter = set(args.concept_ids or []) or None

    def _narrow(mapping: dict[str, Any]) -> dict[str, Any]:
        if concept_filter is None:
            return mapping
        return {key: value for key, value in mapping.items() if key in concept_filter}

    weak = [
        concept_id
        for concept_id in raw.get("weak_concepts", []) or []
        if concept_filter is None or concept_id in concept_filter
    ]
    return StudentProgressResult(
        course_id=str(args.course_id) if args.course_id else raw.get("course_id"),
        mastery={str(k): float(v) for k, v in _narrow(_as_mapping(raw.get("mastery"))).items()},
        attempts={str(k): int(v) for k, v in _narrow(_as_mapping(raw.get("attempts"))).items()},
        last_seen={str(k): str(v) for k, v in _narrow(_as_mapping(raw.get("last_seen"))).items()},
        weak_concepts=weak,
        completed_steps=list(raw.get("completed_steps", []) or []),
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="get_student_progress",
            description=(
                "Mastery, attempt counts, last-seen timestamps and weak concepts "
                "for the calling student, optionally narrowed to concepts."
            ),
            parameters=GetStudentProgressArgs,
            required_permissions=frozenset({Permission.PROGRESS_READ}),
            side_effects="none",
            timeout_ms=2000,
            handler=get_student_progress,
        )
    )


__all__ = ["GetStudentProgressArgs", "StudentProgressResult", "get_student_progress", "register"]
