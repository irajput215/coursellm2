from fastapi import APIRouter
from typing import List

from api.routes.auth import CurrentUserDep, SessionDep
from schemas.evaluation import EvaluationRequest, EvaluationResponse
from services.evaluation_context import resolve_evaluation_context
from services.grading_service import GradingService

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.post("/grade", response_model=EvaluationResponse)
async def grade_student_answer(
    request: EvaluationRequest,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Grade a student's answer against all uploaded material for a course."""
    context = resolve_evaluation_context(session, current_user, request.course_name)
    service = GradingService()
    return await service.grade_submission(request, context, session)


@router.get("/submissions")
async def get_submissions(
    course_name: str,
    current_user: CurrentUserDep,
    session: SessionDep,
    limit: int = 10,
):
    """Get recent submissions for a course."""
    context = resolve_evaluation_context(session, current_user, course_name)
    service = GradingService()
    submissions = await service.get_submission_history(context, session, limit)
    return {"course_name": context.course_name, "submissions": submissions}


@router.get("/progress")
async def get_progress(
    course_name: str,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Get student progress report for a course."""
    context = resolve_evaluation_context(session, current_user, course_name)
    service = GradingService()
    progress = await service.get_student_progress(context, session)
    return progress


@router.post("/batch-grade")
async def batch_grade(
    submissions: List[EvaluationRequest],
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Grade multiple submissions in batch for one or more courses."""
    service = GradingService()
    results = []

    for submission in submissions:
        try:
            context = resolve_evaluation_context(
                session, current_user, submission.course_name
            )
            result = await service.grade_submission(submission, context, session)
            results.append(
                {
                    "course_name": result.course_name,
                    "submission_id": result.submission_id,
                    "score": result.evaluation.total_score,
                    "success": True,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "course_name": submission.course_name,
                    "question": submission.question[:50],
                    "error": str(exc),
                    "success": False,
                }
            )

    return {
        "total": len(submissions),
        "successful": len([item for item in results if item.get("success")]),
        "results": results,
    }
