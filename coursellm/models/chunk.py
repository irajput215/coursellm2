from sqlmodel import SQLModel, Field
from typing import Any
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column

class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    course_id: int = Field(foreign_key="courses.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    content: str
    page: int | None = None
    topic: str | None = None
    chunk_index: int = Field(index=True)
    
    # Vector embedding using pgvector. BAAI/bge-small-en-v1.5 -> 384 dims
    embedding: Any = Field(sa_column=Column(Vector(384)))
