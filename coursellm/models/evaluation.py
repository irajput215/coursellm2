from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import Column, JSON

# Import referenced models for SQLAlchemy metadata
import models.document
import models.course
import models.user
import models.knowledge

class MisconceptionType(str, Enum):
    FACTUAL_ERROR = "factual_error"
    INCOMPLETE = "incomplete"
    CONFUSED_CONCEPT = "confused_concept"
    MISSING_CONTEXT = "missing_context"
    LOGICAL_FALLACY = "logical_fallacy"
    TERMINOLOGY_ERROR = "terminology_error"

class Submission(SQLModel, table=True):
    __tablename__ = "submissions"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    course_id: int = Field(foreign_key="courses.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)
    question: str
    student_answer: str
    expected_answer: Optional[str] = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evaluated: bool = Field(default=False)
    
    # Relationships
    evaluation: Optional["Evaluation"] = Relationship(back_populates="submission")

class Evaluation(SQLModel, table=True):
    __tablename__ = "evaluations"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    submission_id: UUID = Field(foreign_key="submissions.id", unique=True)
    total_score: float = Field(ge=0, le=100)
    rubric_scores: Dict[str, float] = Field(default={}, sa_column=Column(JSON))
    feedback: str
    suggested_topics: List[str] = Field(default=[], sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationships
    submission: Submission = Relationship(back_populates="evaluation")
    misconceptions: List["Misconception"] = Relationship(back_populates="evaluation")

class Misconception(SQLModel, table=True):
    __tablename__ = "misconceptions"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    evaluation_id: UUID = Field(foreign_key="evaluations.id")
    misconception_type: MisconceptionType
    description: str
    corrected_statement: str
    severity: str = Field(default="moderate")  # minor, moderate, severe
    relevant_note_id: Optional[UUID] = Field(default=None, foreign_key="generated_notes.id")
    
    # Relationships
    evaluation: Evaluation = Relationship(back_populates="misconceptions")
