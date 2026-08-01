from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from uuid import UUID, uuid4

class DocumentTopic(SQLModel, table=True):
    __tablename__ = "document_topics"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    topic_name: str
    topic_order: int
    chunk_ids: List[int] = Field(default=[], sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class GeneratedNote(SQLModel, table=True):
    __tablename__ = "generated_notes"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    topic: str
    summary: str
    key_points: List[str] = Field(default=[], sa_column=Column(JSON))
    formulas: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSON))
    examples: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
