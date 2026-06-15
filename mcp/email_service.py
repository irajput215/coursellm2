from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import re
import logging

from dateutil import parser as date_parser

from core.config import settings
from mcp.imap_client import fetch_recent_messages

logger = logging.getLogger(__name__)

ACTIONABLE_KEYWORDS = (
    "assignment",
    "exam",
    "test",
    "quiz",
    "submission",
    "deadline",
    "due",
)

EVENT_TYPE_RULES: list[tuple[str, str, str]] = [
    ("assignment", "assignment", "high"),
    ("exam", "exam", "high"),
    ("quiz", "exam", "medium"),
    ("test", "exam", "medium"),
    ("submission", "deadline", "high"),
    ("deadline", "deadline", "high"),
    ("due", "deadline", "medium"),
]

MONTH_NAMES = (
    "january|february|march|april|may|june|july|"
    "august|september|october|november|december"
)
DATE_CANDIDATE_PATTERNS = [
    rf"(?:due\s+(?:on\s+)?(?:[A-Za-z]+day,?\s+)?)((?:{MONTH_NAMES})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)",
    rf"\b((?:{MONTH_NAMES})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)\b",
    rf"\b(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_NAMES})(?:,?\s+\d{{4}})?)\b",
]
TIME_PATTERN = re.compile(
    r"\b(\d{1,2}:\d{2}\s*(?:AM|PM)|\d{1,2}\s*(?:AM|PM))\b",
    re.IGNORECASE,
)


class Email(BaseModel):
    id: str
    from_address: str
    subject: str
    body: str
    received_at: datetime
    is_course_email: bool = False


class ExtractedEvent(BaseModel):
    title: str
    description: str
    event_type: str
    due_date: Optional[datetime]
    priority: str
    source_email_id: str
    extraction_method: str = "rules"


class EmailService:
    """In-process email ingestion and event extraction service."""

    def _mock_emails(self) -> List[Email]:
        return [
            Email(
                id="email_1",
                from_address="course@unsw.edu.au",
                subject="COMP9044: Assignment 1 Due Next Week",
                body=(
                    "Assignment 1: Unix Filters Implementation is due on "
                    "Friday, June 20th at 5pm."
                ),
                received_at=datetime.utcnow(),
            ),
            Email(
                id="email_2",
                from_address="exams@unsw.edu.au",
                subject="Final Exam Schedule Released",
                body="COMP9044 Final Exam: June 15th, 9:00 AM. Venue: Online.",
                received_at=datetime.utcnow(),
            ),
            Email(
                id="email_3",
                from_address="course@unsw.edu.au",
                subject="COMP9331: Quiz next week",
                body="Reminder: online quiz submission deadline Friday.",
                received_at=datetime.utcnow(),
            ),
            Email(
                id="email_4",
                from_address="news@unsw.edu.au",
                subject="Campus newsletter",
                body="General university updates for all students.",
                received_at=datetime.utcnow(),
            ),
        ]

    def _matches_course(self, course_code: str, subject: str, body: str) -> bool:
        pattern = re.compile(re.escape(course_code), re.IGNORECASE)
        return bool(pattern.search(subject) or pattern.search(body))

    def _is_actionable(self, subject: str, body: str) -> bool:
        text = f"{subject} {body}".lower()
        return any(keyword in text for keyword in ACTIONABLE_KEYWORDS)

    def _classify_event(self, subject: str, body: str) -> tuple[str, str] | None:
        text = f"{subject} {body}".lower()
        for keyword, event_type, priority in EVENT_TYPE_RULES:
            if keyword in text:
                return event_type, priority
        return None

    def _extract_date(self, text: str) -> Optional[datetime]:
        now = datetime.utcnow()
        candidates: list[str] = []
        for pattern in DATE_CANDIDATE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                candidates.append(match.group(1))

        if not candidates:
            return None

        time_match = TIME_PATTERN.search(text)
        time_str = time_match.group(1) if time_match else None
        best: datetime | None = None

        for candidate in candidates:
            try:
                parsed = date_parser.parse(
                    candidate,
                    fuzzy=True,
                    default=now.replace(hour=17, minute=0, second=0, microsecond=0),
                )
                if parsed.tzinfo:
                    parsed = parsed.replace(tzinfo=None)
                if abs(parsed.year - now.year) > 2:
                    continue
                while parsed.date() < now.date():
                    parsed = parsed.replace(year=parsed.year + 1)
                if time_str:
                    parsed_time = date_parser.parse(time_str)
                    parsed = parsed.replace(
                        hour=parsed_time.hour,
                        minute=parsed_time.minute,
                        second=0,
                        microsecond=0,
                    )
                if best is None or parsed < best:
                    best = parsed
            except (ValueError, OverflowError, TypeError):
                continue
        return best

    async def fetch_recent_emails(
        self,
        course_code: str,
        limit: int = 50,
    ) -> List[Email]:
        raw_messages: list[dict]
        if settings.email_use_mock:
            logger.info("Using mock email data for course %s", course_code)
            raw_messages = [
                {
                    "id": item.id,
                    "from_address": item.from_address,
                    "subject": item.subject,
                    "body": item.body,
                    "received_at": item.received_at,
                }
                for item in self._mock_emails()
            ]
        else:
            raw_messages = fetch_recent_messages(limit=limit)

        emails: list[Email] = []
        for message in raw_messages[:limit]:
            subject = message["subject"]
            body = message["body"]
            if not self._matches_course(course_code, subject, body):
                continue
            if not self._is_actionable(subject, body):
                continue
            emails.append(
                Email(
                    id=message["id"],
                    from_address=message["from_address"],
                    subject=subject,
                    body=body,
                    received_at=message["received_at"],
                    is_course_email=True,
                )
            )
        return emails

    def _extract_events_rule_based(self, email: Email) -> List[ExtractedEvent]:
        classification = self._classify_event(email.subject, email.body)
        if not classification:
            return []

        event_type, priority = classification
        combined_text = f"{email.subject}\n{email.body}"
        due_date = self._extract_date(combined_text)

        return [
            ExtractedEvent(
                title=email.subject,
                description=email.body[:200],
                event_type=event_type,
                due_date=due_date,
                priority=priority,
                source_email_id=email.id,
                extraction_method="rules",
            )
        ]

    async def extract_events_from_email(
        self,
        email: Email,
        course_code: str = "",
    ) -> List[ExtractedEvent]:
        if settings.EMAIL_USE_LLM_EXTRACTION and course_code:
            try:
                from agents.email_extractor import extract_events_with_llm

                llm_items = await extract_events_with_llm(
                    course_code=course_code,
                    email_id=email.id,
                    subject=email.subject,
                    body=email.body,
                    from_address=email.from_address,
                    received_at=email.received_at,
                )
                if llm_items:
                    return [ExtractedEvent(**item) for item in llm_items]
            except Exception as exc:
                logger.warning(
                    "LLM email extraction failed for %s, using rules: %s",
                    email.id,
                    exc,
                )

        return self._extract_events_rule_based(email)
