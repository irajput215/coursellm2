"""``create_quiz`` and ``evaluate_answer`` — the assessment write tools.

The handlers are thin: they validate that the executor handed them a
tenant-scoped session, a gateway and an authenticated user, and then call
:class:`~coursellm.assessment.service.AssessmentService`. No prompt, no grading
arithmetic and no SQL lives here, which is what keeps the deterministic part of
assessment behind a typed interface the agent cannot reach around.

The contract is unchanged from the stub this replaces: the same tool names, the
same required permissions, ``side_effects="write"`` and the same timeouts. Only
the behaviour is real now. A model failure still returns a typed degraded result
rather than an invented item or an invented grade, because the service never
raises for a provider failure.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from coursellm.assessment.schemas import (
    AssessmentResult,
    Difficulty,
    ItemType,
    QuizDraft,
    QuizItem,
    RubricCriterion,
)
from coursellm.assessment.service import AssessmentService
from coursellm.db.tenancy import TenantScope
from coursellm.tools.registry import (
    Permission,
    RetryableToolError,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)


def _default_item_types() -> list[ItemType]:
    return ["multiple_choice"]


class CreateQuizArgs(BaseModel):
    """Arguments for ``create_quiz``. ``tenant_id`` is injected, not accepted."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    concept_ids: list[str] = Field(default_factory=list, max_length=50)
    n_items: int = Field(default=5, ge=1, le=20)
    difficulty: Difficulty = "medium"
    item_types: list[ItemType] = Field(default_factory=_default_item_types)


class EvaluateAnswerArgs(BaseModel):
    """Arguments for ``evaluate_answer``."""

    model_config = ConfigDict(extra="forbid")

    quiz_attempt_id: uuid.UUID
    item_id: str = Field(min_length=1, max_length=120)
    answer: str = Field(max_length=10_000)


async def create_quiz(args: CreateQuizArgs, ctx: ToolContext) -> QuizDraft:
    """Generate and persist a grounded quiz draft for the calling student."""
    _require_write_context(ctx, tool="create_quiz")
    assert ctx.session is not None  # narrowed by _require_write_context
    assert ctx.gateway is not None
    assert ctx.user_id is not None
    service = AssessmentService(ctx.session, TenantScope(ctx.tenant_id), ctx.settings)
    return await service.generate(
        user_id=ctx.user_id,
        settings=ctx.settings,
        gateway=ctx.gateway,
        course_id=args.course_id,
        concept_ids=_parse_concept_ids(args.concept_ids),
        n_items=args.n_items,
        difficulty=args.difficulty,
        item_types=args.item_types,
    )


async def evaluate_answer(args: EvaluateAnswerArgs, ctx: ToolContext) -> AssessmentResult:
    """Score one submission, record the attempt and write its progress event."""
    _require_write_context(ctx, tool="evaluate_answer")
    assert ctx.session is not None  # narrowed by _require_write_context
    assert ctx.gateway is not None
    assert ctx.user_id is not None
    service = AssessmentService(ctx.session, TenantScope(ctx.tenant_id), ctx.settings)
    return await service.evaluate_existing_attempt(
        attempt_id=args.quiz_attempt_id,
        item_id=args.item_id,
        answer=args.answer,
        user_id=ctx.user_id,
        settings=ctx.settings,
        gateway=ctx.gateway,
    )


def _require_write_context(ctx: ToolContext, *, tool: str) -> None:
    """Reject a call the executor could not have supplied a full context for.

    A write tool without a session or a gateway cannot do its job, and inventing
    a degraded result would hide an execution wiring bug behind a plausible
    answer.
    """
    if ctx.session is None or ctx.gateway is None:
        msg = f"{tool} requires a tenant-scoped session and a model gateway."
        raise RetryableToolError(msg)
    if ctx.user_id is None:
        msg = f"{tool} requires an authenticated user."
        raise RetryableToolError(msg)


def _parse_concept_ids(values: list[str]) -> list[uuid.UUID]:
    """Parse the caller's concept ids, dropping malformed ones rather than failing.

    A malformed id cannot name a concept in the tenant's graph, so the generator
    would skip it anyway; dropping it here keeps the failure at the boundary
    rather than turning a quiz request into a validation error.
    """
    parsed: list[uuid.UUID] = []
    for value in values:
        try:
            parsed.append(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            continue
    return parsed


def register(registry: ToolRegistry) -> None:
    """Register the two assessment write tools into ``registry``."""
    registry.register(
        ToolSpec(
            name="create_quiz",
            description=(
                "Persist a quiz draft aligned to concepts and grounded in retrieved "
                "passages, with per-item citations and a rubric."
            ),
            parameters=CreateQuizArgs,
            required_permissions=frozenset({Permission.QUIZ_WRITE, Permission.DOCUMENTS_READ}),
            side_effects="write",
            timeout_ms=15_000,
            handler=create_quiz,
        )
    )
    registry.register(
        ToolSpec(
            name="evaluate_answer",
            description=(
                "Score a submitted answer against the item's rubric and record the "
                "mastery delta and misconception tags."
            ),
            parameters=EvaluateAnswerArgs,
            required_permissions=frozenset({Permission.QUIZ_WRITE, Permission.PROGRESS_WRITE}),
            side_effects="write",
            timeout_ms=12_000,
            handler=evaluate_answer,
        )
    )


__all__ = [
    "AssessmentResult",
    "CreateQuizArgs",
    "EvaluateAnswerArgs",
    "QuizDraft",
    "QuizItem",
    "RubricCriterion",
    "create_quiz",
    "evaluate_answer",
    "register",
]
