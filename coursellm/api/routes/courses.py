from fastapi import APIRouter
from typing import List
from sqlmodel import select

from api.routes.auth import CurrentUserDep, SessionDep
from models.course import Course

from pydantic import BaseModel
from datetime import datetime
from models.document import Document

class DocumentInfo(BaseModel):
    id: int
    filename: str
    upload_date: datetime

class CourseWithDocs(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_at: datetime
    documents: List[DocumentInfo] = []

router = APIRouter(tags=["courses"])

@router.get("", response_model=List[CourseWithDocs])
async def get_user_courses(current_user: CurrentUserDep, session: SessionDep):
    """
    Get all courses that belong to the current authenticated user along with their documents.
    """
    courses = session.exec(
        select(Course).where(Course.user_id == current_user.id)
    ).all()
    
    result = []
    for course in courses:
        docs = session.exec(
            select(Document).where(Document.course_id == course.id)
        ).all()
        
        doc_infos = [DocumentInfo(id=d.id, filename=d.filename, upload_date=d.upload_date) for d in docs]
        
        result.append(CourseWithDocs(
            id=course.id,
            name=course.name,
            description=course.description,
            created_at=course.created_at,
            documents=doc_infos
        ))
        
    return result
