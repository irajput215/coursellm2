from fastapi import APIRouter
from typing import List
from sqlmodel import select

from api.routes.auth import CurrentUserDep, SessionDep
from models.course import Course

router = APIRouter(tags=["courses"])

@router.get("", response_model=List[Course])
async def get_user_courses(current_user: CurrentUserDep, session: SessionDep):
    """
    Get all courses that belong to the current authenticated user.
    """
    courses = session.exec(
        select(Course).where(Course.user_id == current_user.id)
    ).all()
    return courses
