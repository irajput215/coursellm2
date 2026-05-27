from dataclasses import dataclass

from fastapi import HTTPException
from sqlmodel import Session, select

from models.course import Course
from models.document import Document
from models.user import User


@dataclass(frozen=True)
class EvaluationContext:
    course_id: int
    course_name: str
    user_id: int
    document_ids: list[int]


def resolve_evaluation_context(
    session: Session,
    user: User,
    course_name: str,
) -> EvaluationContext:
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

    documents = session.exec(
        select(Document).where(
            Document.course_id == course.id,
            Document.user_id == user.id,
        )
    ).all()

    document_ids = [doc.id for doc in documents if doc.id is not None]
    if not document_ids:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No documents uploaded for course '{course_name}'. "
                "Upload course material first."
            ),
        )

    return EvaluationContext(
        course_id=course.id,
        course_name=course_name,
        user_id=user.id,
        document_ids=document_ids,
    )
