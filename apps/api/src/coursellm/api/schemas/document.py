"""Request and response schemas for the documents domain.

Response models are written independently of the ORM models, as elsewhere in
this codebase, so that adding a sensitive column to ``documents`` cannot
silently publish it. ``storage_key`` and ``user_id`` are deliberately absent:
the first is a server-side implementation detail, and the second is always the
caller.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    """One document as returned by the list endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    sha256: str
    status: str
    source_type: str
    page_count: int | None
    quarantine_state: str
    injection_score: float
    error_message: str | None
    created_at: datetime


class DocumentIngestionResponse(DocumentResponse):
    """The result of an upload, including what ingestion actually did.

    ``reused`` distinguishes "these exact bytes were already in this course" from
    a fresh ingest, so a client can tell a duplicate upload apart from a new
    document without a second request. ``degraded`` carries parser warnings
    (unreadable pages, no extractable text) so a partially useful upload is
    visible rather than silently incomplete.
    """

    chunks_processed: int = Field(
        description="Chunks written, or already present on a reused document; 0 when quarantined."
    )
    reused: bool
    degraded: list[str] = Field(default_factory=list)


class DocumentDetailResponse(DocumentResponse):
    """One document's full ingestion state."""

    chunk_count: int
    injection_classes: list[str] = Field(default_factory=list)


__all__ = [
    "DocumentDetailResponse",
    "DocumentIngestionResponse",
    "DocumentResponse",
]
