from datetime import datetime
import logging
from typing import List, Optional

from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from pydantic_ai import Agent

logger = logging.getLogger(__name__)

ALLOWED_EVENT_TYPES = (
    "assignment",
    "exam",
    "lecture",
    "review",
    "deadline",
    "meeting",
)
ALLOWED_PRIORITIES = ("high", "medium", "low")


class LLMEventItem(BaseModel):
    title: str = Field(description="Short calendar title for the event")
    description: str = Field(
        description="1-2 sentence summary of what the student needs to do"
    )
    event_type: str = Field(
        description="One of: assignment, exam, lecture, review, deadline, meeting"
    )
    priority: str = Field(description="One of: high, medium, low")
    due_date_iso: Optional[str] = Field(
        default=None,
        description="ISO-8601 datetime for the due date or exam time, if known",
    )
    is_actionable: bool = Field(
        description="True if this is a real course deadline the student should track"
    )


class EmailExtractionOutput(BaseModel):
    events: List[LLMEventItem] = Field(
        default_factory=list,
        description="Zero or more planner events extracted from the email",
    )


email_extractor_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=EmailExtractionOutput,
    retries=3,
)


def _parse_due_date(
    due_date_iso: Optional[str],
    reference: datetime,
) -> Optional[datetime]:
    if not due_date_iso:
        return None
    try:
        parsed = date_parser.isoparse(due_date_iso)
        if parsed.tzinfo:
            parsed = parsed.replace(tzinfo=None)
        while parsed.date() < reference.date():
            parsed = parsed.replace(year=parsed.year + 1)
        return parsed
    except (ValueError, TypeError, OverflowError):
        try:
            parsed = date_parser.parse(due_date_iso, fuzzy=True, default=reference)
            if parsed.tzinfo:
                parsed = parsed.replace(tzinfo=None)
            while parsed.date() < reference.date():
                parsed = parsed.replace(year=parsed.year + 1)
            return parsed
        except (ValueError, TypeError, OverflowError):
            return None


def _normalize_event_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in ALLOWED_EVENT_TYPES:
        return normalized
    if normalized in ("test", "quiz", "midterm", "final"):
        return "exam"
    if normalized in ("submission", "submit", "due"):
        return "deadline"
    return "deadline"


def _normalize_priority(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in ALLOWED_PRIORITIES:
        return normalized
    return "medium"


async def extract_events_with_llm(
    *,
    course_code: str,
    email_id: str,
    subject: str,
    body: str,
    from_address: str,
    received_at: datetime,
) -> List[dict]:
    """
    Extract planner events from an email using the LLM.
    Returns a list of dicts compatible with ExtractedEvent construction.
    """
    prompt = f"""
Extract academic planner events from this university email for course {course_code}.

EMAIL METADATA:
- From: {from_address}
- Received: {received_at.isoformat()}
- Subject: {subject}

BODY:
{body[:4000]}

Rules:
- Only extract events relevant to {course_code}.
- Include assignments, exams, tests, quizzes, submissions, deadlines, lectures, or review sessions.
- Skip newsletters, social events, and generic announcements with no student action.
- Use event_type: assignment, exam, lecture, review, deadline, or meeting.
- Set priority high for exams and major assignments, medium for tests/quizzes, low for optional items.
- Put due dates in due_date_iso (ISO-8601) when a date or datetime is mentioned.
- If the year is missing, choose the nearest future date relative to received time.
- An email may contain multiple events.
- If nothing actionable exists, return an empty events list.
"""

    result = await email_extractor_agent.run(prompt)
    output = result.output
    extracted: list[dict] = []

    for item in output.events:
        if not item.is_actionable:
            continue
        due_date = _parse_due_date(item.due_date_iso, received_at)
        extracted.append(
            {
                "title": item.title.strip() or subject,
                "description": item.description.strip() or body[:200],
                "event_type": _normalize_event_type(item.event_type),
                "priority": _normalize_priority(item.priority),
                "due_date": due_date,
                "source_email_id": email_id,
                "extraction_method": "llm",
            }
        )

    return extracted
