from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime, timezone
from uuid import UUID, uuid4

class GenerationStatus(SQLModel, table=True):
    __tablename__ = "generation_status"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    task_type: str  # 'topics', 'notes', 'flashcards', 'quizzes'
    status: str     # 'pending', 'processing', 'completed', 'failed'
    depends_on: Optional[UUID] = Field(default=None)
    retry_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
