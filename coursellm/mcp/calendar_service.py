from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from database import engine
from models.planner import PlannerEvent, EventType, Priority
import logging

logger = logging.getLogger(__name__)


class CalendarService:
    """In-process planner calendar persistence service."""

    def _event_lookup_query(
        self,
        user_id: str,
        source_email_id: str,
        event_type: EventType,
        document_id: Optional[int] = None,
    ):
        query = select(PlannerEvent).where(
            PlannerEvent.user_id == user_id,
            PlannerEvent.source_email_id == source_email_id,
            PlannerEvent.event_type == event_type,
        )
        if document_id is not None:
            query = query.where(PlannerEvent.document_id == document_id)
        return query

    async def get_event_by_source(
        self,
        user_id: str,
        source_email_id: str,
        event_type: EventType,
        document_id: Optional[int] = None,
    ) -> Optional[PlannerEvent]:
        with Session(engine) as session:
            return session.exec(
                self._event_lookup_query(
                    user_id, source_email_id, event_type, document_id
                )
            ).first()

    async def event_exists(
        self,
        user_id: str,
        source_email_id: str,
        event_type: EventType,
        document_id: Optional[int] = None,
    ) -> bool:
        existing = await self.get_event_by_source(
            user_id, source_email_id, event_type, document_id
        )
        return existing is not None

    async def update_event(
        self,
        event_id: UUID,
        title: str,
        description: str,
        due_date: datetime,
        priority: Priority = Priority.MEDIUM,
    ) -> Optional[PlannerEvent]:
        with Session(engine) as session:
            event = session.get(PlannerEvent, event_id)
            if not event:
                return None
            event.title = title
            event.description = description
            event.due_date = due_date
            event.start_time = due_date
            event.priority = priority
            event.updated_at = datetime.utcnow()
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    async def add_event(
        self,
        user_id: str,
        title: str,
        description: str,
        event_type: EventType,
        due_date: datetime,
        priority: Priority = Priority.MEDIUM,
        source_email_id: Optional[str] = None,
        document_id: Optional[int] = None,
    ) -> PlannerEvent:
        with Session(engine) as session:
            event = PlannerEvent(
                user_id=user_id,
                title=title,
                description=description,
                event_type=event_type,
                priority=priority,
                due_date=due_date,
                start_time=due_date,
                source_email_id=source_email_id,
                document_id=document_id,
            )
            session.add(event)
            session.commit()
            session.refresh(event)

            logger.info("Added event: %s for user %s", title, user_id)
            return event

    async def get_upcoming_events(
        self,
        user_id: str,
        days_ahead: int = 14,
        document_id: Optional[int] = None,
    ) -> List[PlannerEvent]:
        now = datetime.utcnow()
        future = now + timedelta(days=days_ahead)

        with Session(engine) as session:
            query = (
                select(PlannerEvent)
                .where(
                    PlannerEvent.user_id == user_id,
                    PlannerEvent.due_date >= now,
                    PlannerEvent.due_date <= future,
                    PlannerEvent.completed == False,
                )
                .order_by(PlannerEvent.due_date)
            )
            if document_id is not None:
                query = query.where(PlannerEvent.document_id == document_id)
            return session.exec(query).all()

    async def get_all_events_for_document(
        self,
        user_id: str,
        document_id: int,
    ) -> List[PlannerEvent]:
        with Session(engine) as session:
            return session.exec(
                select(PlannerEvent)
                .where(
                    PlannerEvent.user_id == user_id,
                    PlannerEvent.document_id == document_id,
                )
                .order_by(PlannerEvent.due_date)
            ).all()

    async def get_events_by_week(
        self,
        user_id: str,
        week_start: datetime,
        document_id: Optional[int] = None,
    ) -> List[PlannerEvent]:
        week_end = week_start + timedelta(days=7)

        with Session(engine) as session:
            query = (
                select(PlannerEvent)
                .where(
                    PlannerEvent.user_id == user_id,
                    PlannerEvent.due_date >= week_start,
                    PlannerEvent.due_date < week_end,
                )
                .order_by(PlannerEvent.due_date)
            )
            if document_id is not None:
                query = query.where(PlannerEvent.document_id == document_id)
            return session.exec(query).all()

    async def mark_completed(self, event_id: UUID) -> bool:
        with Session(engine) as session:
            event = session.get(PlannerEvent, event_id)
            if event:
                event.completed = True
                event.updated_at = datetime.utcnow()
                session.commit()
                return True
            return False
