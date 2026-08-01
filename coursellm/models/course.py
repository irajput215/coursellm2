from sqlmodel import SQLModel, Field
from datetime import datetime

class Course(SQLModel, table=True):
    __tablename__ = "courses"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(index=True)
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
