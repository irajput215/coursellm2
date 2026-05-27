from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from uuid import UUID
from datetime import datetime
from enum import Enum

class MisconceptionType(str, Enum):
    FACTUAL_ERROR = "factual_error"
    INCOMPLETE = "incomplete"
    CONFUSED_CONCEPT = "confused_concept"
    MISSING_CONTEXT = "missing_context"
    LOGICAL_FALLACY = "logical_fallacy"
    TERMINOLOGY_ERROR = "terminology_error"

class RubricCriteria(BaseModel):
    name: str
    weight: float = Field(ge=0, le=1)
    max_score: float = Field(ge=0, le=10)

class Rubric(BaseModel):
    criteria: List[RubricCriteria] = [
        RubricCriteria(name="factual_accuracy", weight=0.35, max_score=10),
        RubricCriteria(name="relevance", weight=0.25, max_score=10),
        RubricCriteria(name="coherence", weight=0.20, max_score=10),
        RubricCriteria(name="completeness", weight=0.20, max_score=10),
    ]

class MisconceptionSchema(BaseModel):
    type: MisconceptionType
    description: str
    corrected_statement: str
    severity: str = "moderate"
    relevant_note_id: Optional[UUID] = None

class EvaluationResult(BaseModel):
    total_score: float = Field(ge=0, le=100)
    rubric_scores: Dict[str, float]
    misconceptions: List[MisconceptionSchema]
    feedback: str
    suggested_topics: List[str]

class EvaluationRequest(BaseModel):
    question: str
    student_answer: str
    course_name: str = Field(..., min_length=1, examples=["COMP9044"])
    expected_answer: Optional[str] = None
    custom_rubric: Optional[Rubric] = None


class EvaluationResponse(BaseModel):
    submission_id: UUID
    course_name: str
    document_ids_used: List[int]
    evaluation: EvaluationResult
    created_at: datetime
