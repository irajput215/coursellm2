import dramatiq
import workers.base
from services.grading_service import GradingService
from schemas.evaluation import EvaluationRequest
from services.evaluation_context import resolve_evaluation_context
from database import engine
from sqlmodel import Session
from models.user import User
import asyncio
import logging

logger = logging.getLogger(__name__)


@dramatiq.actor(max_retries=3, time_limit=60000)
def grade_submission_task(
    question: str,
    student_answer: str,
    course_name: str,
    user_id: int,
    expected_answer: str = None,
):
    """Background task for grading a single submission."""

    async def _grade():
        with Session(engine) as session:
            user = session.get(User, user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")
            context = resolve_evaluation_context(session, user, course_name)
            service = GradingService()
            request = EvaluationRequest(
                question=question,
                student_answer=student_answer,
                course_name=course_name,
                expected_answer=expected_answer,
            )
            return await service.grade_submission(request, context, session)

    try:
        result = asyncio.run(_grade())
        logger.info(
            "Graded submission %s for %s: %s",
            result.submission_id,
            course_name,
            result.evaluation.total_score,
        )
        return str(result.submission_id)

    except Exception as exc:
        logger.error("Grading failed: %s", exc)
        raise


@dramatiq.actor
def batch_grade_task(submissions: list):
    """Background task for batch grading."""
    results = []

    for sub in submissions:
        try:
            result = grade_submission_task(
                question=sub["question"],
                student_answer=sub["student_answer"],
                course_name=sub["course_name"],
                user_id=sub["user_id"],
                expected_answer=sub.get("expected_answer"),
            )
            results.append(result)
        except Exception as exc:
            logger.error("Failed on submission: %s", exc)
            results.append({"error": str(exc), "failed": True})

    return results
