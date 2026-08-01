from dataclasses import dataclass

from fastapi import HTTPException
from sqlmodel import Session, select

from models.course import Course
from models.document import Document
from models.user import User


@dataclass(frozen=True)
class PlannerContext:
    course_id: int
    document_id: int
    course_name: str


def resolve_planner_context(
    session: Session,
    user: User,
    course_name: str,
) -> PlannerContext:
    course = session.exec(
        select(Course).where(
            Course.name == course_name,
            Course.user_id == user.id,
        )
    ).first()
    if not course or course.id is None:
        raise HTTPException(
            status_code=404,
            detail=f"Course '{course_name}' not found",
        )

    document = session.exec(
        select(Document)
        .where(
            Document.course_id == course.id,
            Document.user_id == user.id,
        )
        .order_by(Document.upload_date.desc())
    ).first()

    if not document or document.id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No documents uploaded for course '{course_name}'. "
                "Upload course material first."
            ),
        )

    return PlannerContext(
        course_id=course.id,
        document_id=document.id,
        course_name=course_name,
    )
