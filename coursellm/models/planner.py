from sqlmodel import SQLModel, Field
from typing import Optional, List, Dict
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum
from sqlalchemy import Column, JSON
import models.document

class EventType(str, Enum):
    ASSIGNMENT = "assignment"
    EXAM = "exam"
    LECTURE = "lecture"
    REVIEW = "review"
    DEADLINE = "deadline"
    MEETING = "meeting"

class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class PlannerEvent(SQLModel, table=True):
    __tablename__ = "planner_events"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True)
    title: str
    description: Optional[str] = None
    event_type: EventType
    priority: Priority = Priority.MEDIUM
    start_time: datetime
    end_time: Optional[datetime] = None
    due_date: Optional[datetime] = None
    source_email_id: Optional[str] = None
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id")
    completed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class StudyPlan(SQLModel, table=True):
    __tablename__ = "study_plans"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True)
    document_id: int = Field(foreign_key="documents.id")
    week_number: int
    topics: List[str] = Field(default=[], sa_column=Column(JSON))
    estimated_hours: float
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

class Reminder(SQLModel, table=True):
    __tablename__ = "reminders"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    event_id: UUID = Field(foreign_key="planner_events.id")
    reminder_time: datetime
    sent: bool = False
    reminder_type: str = "email"  # email, push, both
    created_at: datetime = Field(default_factory=datetime.utcnow)
