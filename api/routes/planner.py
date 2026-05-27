from datetime import datetime

from fastapi import APIRouter

from agents.planner_agent import PlannerAgent
from api.routes.auth import CurrentUserDep, SessionDep
from mcp.calendar_mcp import CalendarMCP
from schemas.planner import ProcessEmailsRequest, StudyPlanRequest
from services.planner_context import resolve_planner_context

router = APIRouter(prefix="/planner", tags=["planner"])


def _user_id(current_user: CurrentUserDep) -> str:
    return str(current_user.id)


def _serialize_event(event) -> dict:
    return {
        "id": str(event.id),
        "title": event.title,
        "description": event.description,
        "due_date": event.due_date.isoformat() if event.due_date else None,
        "event_type": event.event_type.value,
        "priority": event.priority.value,
        "completed": event.completed,
        "source_email_id": event.source_email_id,
    }


@router.get("/email-preview")
async def preview_emails(
    course_name: str,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Dry-run: show course emails and extracted events without writing to the calendar.
    Use this to verify filtering before calling /process-emails.
    """
    resolve_planner_context(session, current_user, course_name)
    agent = PlannerAgent()
    previews = await agent.preview_incoming_emails(course_name)
    return {
        "course_name": course_name,
        "emails_matched": len(previews),
        "previews": previews,
    }


@router.get("/sync-status")
async def sync_status(
    course_name: str,
    current_user: CurrentUserDep,
    session: SessionDep,
    days_ahead: int = 30,
):
    """Calendar snapshot for a course: upcoming, past, and completed counts."""
    context = resolve_planner_context(session, current_user, course_name)
    calendar = CalendarMCP()
    user_id = _user_id(current_user)

    upcoming = await calendar.get_upcoming_events(
        user_id, days_ahead=days_ahead, document_id=context.document_id
    )
    all_events = await calendar.get_all_events_for_document(
        user_id, document_id=context.document_id
    )

    now = datetime.utcnow()
    past = [
        e for e in all_events
        if e.due_date and e.due_date < now and not e.completed
    ]
    completed = [e for e in all_events if e.completed]

    return {
        "course_name": context.course_name,
        "document_id": context.document_id,
        "days_ahead": days_ahead,
        "counts": {
            "total": len(all_events),
            "upcoming": len(upcoming),
            "past_incomplete": len(past),
            "completed": len(completed),
        },
        "upcoming_events": [_serialize_event(e) for e in upcoming],
    }


@router.post("/process-emails")
async def process_emails(
    request: ProcessEmailsRequest,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Fetch course-related emails and add planner events for the course."""
    context = resolve_planner_context(session, current_user, request.course_name)
    agent = PlannerAgent()

    result = await agent.process_incoming_emails(
        user_id=_user_id(current_user),
        course_name=context.course_name,
        document_id=context.document_id,
        force_refresh=request.force_refresh,
    )
    return result


@router.post("/generate-study-plan")
async def generate_study_plan(
    request: StudyPlanRequest,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Generate a study plan for a course."""
    context = resolve_planner_context(session, current_user, request.course_name)
    agent = PlannerAgent()

    return await agent.generate_study_plan(
        user_id=_user_id(current_user),
        course_name=context.course_name,
        document_id=context.document_id,
        weeks=request.weeks,
    )


@router.get("/daily-briefing")
async def get_daily_briefing(
    course_name: str,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Get today's briefing for a course."""
    context = resolve_planner_context(session, current_user, course_name)
    agent = PlannerAgent()
    briefing = await agent.get_daily_briefing(
        _user_id(current_user),
        context.document_id,
    )
    return {
        "course_name": context.course_name,
        "document_id": context.document_id,
        "briefing": briefing,
    }


@router.get("/events")
async def get_events(
    course_name: str,
    current_user: CurrentUserDep,
    session: SessionDep,
    days_ahead: int = 14,
):
    """Get upcoming planner events for a course."""
    context = resolve_planner_context(session, current_user, course_name)
    calendar = CalendarMCP()
    events = await calendar.get_upcoming_events(
        _user_id(current_user),
        days_ahead=days_ahead,
        document_id=context.document_id,
    )

    return {
        "course_name": context.course_name,
        "document_id": context.document_id,
        "days_ahead": days_ahead,
        "events": [_serialize_event(e) for e in events],
    }
