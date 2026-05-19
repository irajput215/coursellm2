from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional

class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    course_id: Optional[int] = Field(default=None, foreign_key="courses.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Message(SQLModel, table=True):
    __tablename__ = "messages"
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="chat_sessions.id", index=True)
    role: str = Field(index=True) # 'user', 'assistant'
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
