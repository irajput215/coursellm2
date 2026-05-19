from sqlmodel import SQLModel, Field
from datetime import datetime

class Document(SQLModel, table=True):
    __tablename__ = "documents"
    id: int | None = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="courses.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    filename: str
    upload_date: datetime = Field(default_factory=datetime.utcnow)
