from pydantic_ai import Agent
from typing import Dict, List, Any
from datetime import datetime, timedelta

from mcp.email_service import EmailService
from mcp.calendar_service import CalendarService
from agents.study_planner import StudyPlanGenerator
from models.planner import EventType, Priority, PlannerEvent
from sqlmodel import Session, select
from database import engine
import logging

logger = logging.getLogger(__name__)


class PlannerAgent:
    """Main orchestrator for all planning workflows."""

    def __init__(self):
        self.email_service = EmailService()
        self.calendar_service = CalendarService()
        self.study_planner = StudyPlanGenerator()

    async def preview_incoming_emails(
        self,
        course_name: str,
    ) -> List[Dict[str, Any]]:
        """Dry-run: show matched emails and extracted events without writing to DB."""
        previews: list[dict[str, Any]] = []
        emails = await self.email_service.fetch_recent_emails(course_name)

        for email in emails:
            events = await self.email_service.extract_events_from_email(
                email, course_name
            )
            previews.append(
                {
                    "email_id": email.id,
                    "subject": email.subject,
                    "from_address": email.from_address,
                    "received_at": email.received_at.isoformat(),
                    "extracted_events": [
                        {
                            "title": event.title,
                            "event_type": event.event_type,
                            "priority": event.priority,
                            "due_date": event.due_date.isoformat()
                            if event.due_date
                            else None,
                            "source_email_id": event.source_email_id,
                            "extraction_method": event.extraction_method,
                        }
                        for event in events
                    ],
                }
            )
        return previews

    async def process_incoming_emails(
        self,
        user_id: str,
        course_name: str,
        document_id: int,
        force_refresh: bool = False,
    ) -> Dict:
        """
        Workflow 1: Process emails -> extract events -> add to calendar
        """
        results = {
            "course_name": course_name,
            "document_id": document_id,
            "emails_processed": 0,
            "events_added": 0,
            "events_updated": 0,
            "events_skipped_duplicate": 0,
            "errors": [],
            "details": [],
        }

        try:
            emails = await self.email_service.fetch_recent_emails(course_name)
            results["emails_processed"] = len(emails)

            for email in emails:
                events = await self.email_service.extract_events_from_email(
                email, course_name
            )

                for event in events:
                    try:
                        event_type = EventType(event.event_type)
                        due_date = event.due_date or datetime.utcnow() + timedelta(
                            days=7
                        )
                        priority = Priority(event.priority)
                        existing = await self.calendar_service.get_event_by_source(
                            user_id=user_id,
                            source_email_id=event.source_email_id,
                            event_type=event_type,
                            document_id=document_id,
                        )
                        if existing:
                            if force_refresh or (
                                existing.due_date != due_date
                                or existing.title != event.title
                                or existing.description != event.description
                            ):
                                updated = await self.calendar_service.update_event(
                                    event_id=existing.id,
                                    title=event.title,
                                    description=event.description,
                                    due_date=due_date,
                                    priority=priority,
                                )
                                results["events_updated"] += 1
                                results["details"].append(
                                    {
                                        "action": "updated",
                                        "title": event.title,
                                        "event_type": event.event_type,
                                        "due_date": due_date.isoformat(),
                                        "event_id": str(updated.id) if updated else None,
                                        "extraction_method": event.extraction_method,
                                    }
                                )
                            else:
                                results["events_skipped_duplicate"] += 1
                                results["details"].append(
                                    {
                                        "action": "unchanged",
                                        "title": event.title,
                                        "event_type": event.event_type,
                                        "due_date": due_date.isoformat(),
                                        "event_id": str(existing.id),
                                        "extraction_method": event.extraction_method,
                                    }
                                )
                            continue

                        calendar_event = await self.calendar_service.add_event(
                            user_id=user_id,
                            title=event.title,
                            description=event.description,
                            event_type=event_type,
                            due_date=due_date,
                            priority=priority,
                            source_email_id=event.source_email_id,
                            document_id=document_id,
                        )
                        results["events_added"] += 1
                        results["details"].append(
                            {
                                "action": "added",
                                "title": event.title,
                                "event_type": event.event_type,
                                "due_date": due_date.isoformat(),
                                "event_id": str(calendar_event.id),
                                "extraction_method": event.extraction_method,
                            }
                        )
                        logger.info("Added event: %s", calendar_event.title)

                    except Exception as exc:
                        results["errors"].append(f"Failed for {email.id}: {exc}")

            upcoming = await self.calendar_service.get_upcoming_events(
                user_id,
                days_ahead=30,
                document_id=document_id,
            )
            results["upcoming_events_count"] = len(upcoming)
            results["upcoming_events"] = [
                {
                    "id": str(event.id),
                    "title": event.title,
                    "due_date": event.due_date.isoformat() if event.due_date else None,
                    "event_type": event.event_type.value,
                }
                for event in upcoming
            ]

            return results

        except Exception as exc:
            logger.error("Email processing failed: %s", exc)
            raise

    async def generate_study_plan(
        self,
        user_id: str,
        course_name: str,
        document_id: int,
        weeks: int = 4,
    ) -> Dict:
        """
        Workflow 2: Generate study plan based on upcoming events and course content
        """
        upcoming = await self.calendar_service.get_upcoming_events(
            user_id,
            days_ahead=weeks * 7,
            document_id=document_id,
        )

        events_data = [
            {
                "title": event.title,
                "due_date": event.due_date.isoformat() if event.due_date else None,
                "event_type": event.event_type.value,
            }
            for event in upcoming
        ]

        plan = await self.study_planner.generate_plan(
            user_id=user_id,
            document_id=document_id,
            upcoming_events=events_data,
            weeks=weeks,
        )

        reminders_created = []
        for event in upcoming:
            if event.due_date and not event.completed:
                reminder_time = event.due_date - timedelta(days=2)
                if reminder_time > datetime.utcnow():
                    reminders_created.append(
                        {
                            "event": event.title,
                            "reminder_at": reminder_time.isoformat(),
                        }
                    )

        return {
            "course_name": course_name,
            "document_id": document_id,
            "study_plan": plan.model_dump(),
            "reminders_scheduled": reminders_created,
            "total_events_considered": len(upcoming),
        }

    async def get_daily_briefing(self, user_id: str, document_id: int) -> str:
        """
        Workflow 3: Generate daily briefing with tasks and reminders
        """
        today = datetime.utcnow().date()
        tomorrow = today + timedelta(days=1)

        with Session(engine) as session:
            today_events = session.exec(
                select(PlannerEvent)
                .where(
                    PlannerEvent.user_id == user_id,
                    PlannerEvent.document_id == document_id,
                    PlannerEvent.due_date >= datetime.combine(today, datetime.min.time()),
                    PlannerEvent.due_date < datetime.combine(tomorrow, datetime.min.time()),
                    PlannerEvent.completed == False,
                )
            ).all()

            upcoming_deadlines = session.exec(
                select(PlannerEvent)
                .where(
                    PlannerEvent.user_id == user_id,
                    PlannerEvent.document_id == document_id,
                    PlannerEvent.due_date > datetime.combine(tomorrow, datetime.min.time()),
                    PlannerEvent.due_date < datetime.utcnow() + timedelta(days=7),
                    PlannerEvent.completed == False,
                )
                .order_by(PlannerEvent.due_date)
                .limit(5)
            ).all()

        briefing_agent = Agent("groq:llama-3.3-70b-versatile", retries=3)

        prompt = f"""
        Create a daily briefing for today ({today}).

        TODAY'S TASKS:
        {[{'title': event.title, 'due': event.due_date} for event in today_events]}

        UPCOMING DEADLINES (next 7 days):
        {[{'title': event.title, 'due': event.due_date} for event in upcoming_deadlines]}

        Generate a concise, encouraging briefing that:
        1. Lists today's priority tasks
        2. Warns about upcoming deadlines
        3. Suggests what to focus on
        """

        result = await briefing_agent.run(prompt)

        return f"Daily Briefing for {today}\n\n{result.output}"
