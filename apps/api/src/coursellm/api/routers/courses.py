"""Course endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from coursellm.api.deps import (
    ContextDep,
    CourseRepositoryDep,
    DocumentRepositoryDep,
)
from coursellm.core.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=2000)


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    page_count: int | None
    status: str
    source_type: str
    quarantine_state: str


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str | None
    description: str | None
    created_at: object


class CourseDetailResponse(CourseResponse):
    documents: list[DocumentSummary]


@router.post(
    "",
    response_model=CourseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a course",
)
async def create_course(
    payload: CourseCreateRequest,
    context: ContextDep,
    courses: CourseRepositoryDep,
) -> CourseResponse:
    if await courses.get_by_name(payload.name) is not None:
        raise ConflictError("A course with that name already exists in this workspace.")
    course = await courses.create(
        user_id=context.user_id,
        name=payload.name,
        code=payload.code,
        description=payload.description,
    )
    await courses.flush()
    return CourseResponse.model_validate(course)


@router.get(
    "",
    response_model=list[CourseResponse],
    summary="List the caller's courses",
)
async def list_courses(
    context: ContextDep,
    courses: CourseRepositoryDep,
) -> list[CourseResponse]:
    rows = await courses.list_for_user(context.user_id)
    return [CourseResponse.model_validate(c) for c in rows]


@router.get(
    "/{course_id}",
    response_model=CourseDetailResponse,
    summary="A course and its documents",
    description=(
        "A course belonging to another tenant or another user is reported as not "
        "found. Reporting it as forbidden would confirm that it exists."
    ),
)
async def get_course(
    course_id: uuid.UUID,
    context: ContextDep,
    courses: CourseRepositoryDep,
    documents: DocumentRepositoryDep,
) -> CourseDetailResponse:
    course = await courses.get_owned(course_id, context.user_id)
    if course is None:
        raise NotFoundError("Course not found.")
    docs = await documents.list_for_course(course.id)
    return CourseDetailResponse(
        id=course.id,
        name=course.name,
        code=course.code,
        description=course.description,
        created_at=course.created_at,
        documents=[DocumentSummary.model_validate(d) for d in docs],
    )


@router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a course and everything in it",
)
async def delete_course(
    course_id: uuid.UUID,
    context: ContextDep,
    courses: CourseRepositoryDep,
) -> None:
    course = await courses.get_owned(course_id, context.user_id)
    if course is None:
        raise NotFoundError("Course not found.")
    # Cascades to documents, chunks, embeddings and lexical statistics through
    # the foreign keys, so no child rows can be orphaned by a partial delete.
    await courses.delete(course.id)
