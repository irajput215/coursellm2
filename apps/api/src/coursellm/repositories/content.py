"""Repositories for courses and documents."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import selectinload

from coursellm.db.models.content import Course, Document, DocumentStatus, SourceType
from coursellm.repositories.base import TenantRepository


class CourseRepository(TenantRepository[Course]):
    model = Course

    async def get_by_name(self, name: str) -> Course | None:
        stmt = self._select().where(Course.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        code: str | None = None,
        description: str | None = None,
    ) -> Course:
        course = Course(user_id=user_id, name=name, code=code, description=description)
        return self.add(course)

    async def list_for_user(
        self, user_id: uuid.UUID, *, with_documents: bool = False, limit: int = 100
    ) -> list[Course]:
        stmt = self._select().where(Course.user_id == user_id)
        if with_documents:
            # Eager-loaded rather than lazy: the ORM relationships are declared
            # ``lazy="raise"`` precisely so that an accidental N+1 in a request
            # handler fails loudly instead of issuing a query per row, which is
            # what the previous implementation did on its course list endpoint.
            stmt = stmt.options(selectinload(Course.documents))
        stmt = stmt.order_by(Course.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_owned(self, course_id: uuid.UUID, user_id: uuid.UUID) -> Course | None:
        stmt = self._select().where(Course.id == course_id, Course.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class DocumentRepository(TenantRepository[Document]):
    model = Document

    async def get_by_sha256(self, course_id: uuid.UUID, sha256: str) -> Document | None:
        """Find an existing document by content hash.

        Deduplication is by content, not filename, so that re-uploading the same
        file is idempotent instead of creating a second copy whose chunks double
        every retrieval result.
        """
        stmt = self._select().where(Document.course_id == course_id, Document.sha256 == sha256)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        course_id: uuid.UUID,
        user_id: uuid.UUID,
        filename: str,
        storage_key: str,
        sha256: str,
        size_bytes: int,
        content_type: str | None = None,
        source_type: SourceType = SourceType.OTHER,
    ) -> Document:
        document = Document(
            course_id=course_id,
            user_id=user_id,
            filename=filename,
            storage_key=storage_key,
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=content_type,
            source_type=source_type,
            status=DocumentStatus.PENDING,
        )
        return self.add(document)

    async def list_for_course(self, course_id: uuid.UUID, *, limit: int = 200) -> list[Document]:
        stmt = (
            self._select()
            .where(Document.course_id == course_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_ready(self, course_id: uuid.UUID | None = None) -> list[Document]:
        """Documents eligible for retrieval.

        Quarantined documents are excluded here rather than filtered at query
        time, so a document flagged as a prompt-injection carrier cannot be
        retrieved even by a code path that forgets to check.
        """
        stmt = self._select().where(Document.status == DocumentStatus.READY)
        if course_id is not None:
            stmt = stmt.where(Document.course_id == course_id)
        result = await self._session.execute(stmt)
        return [d for d in result.scalars().all() if d.quarantine_state != "quarantined"]
