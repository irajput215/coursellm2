"""Document endpoints: upload, list, read, delete.

This is the router that finally connects the ingestion pipeline to the product.
Four behaviours are choices rather than accidents and are documented on the
routes themselves:

* **A duplicate upload is the same document.** Re-uploading identical bytes into
  the same course returns the existing row with ``200`` and ``reused=true``,
  because a second copy would double every retrieval result for those bytes.
* **``course_name`` creates the course when it is absent**, matching
  ``POST /courses``. This is stated in the endpoint description so a typo
  produces a visible new course rather than a silent one; an explicit
  ``course_id`` is the alternative for callers that must not create anything.
* **Another tenant's or another user's document is a 404**, never a 403.
  Reporting "forbidden" would confirm that the row exists.
* **Deleting removes the row and then the object.** A failure to remove the
  object is logged and the request still succeeds: the user's document is gone,
  and an unreferenced blob is a cleanup job, not a failed action.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from coursellm.api.deps import ContextDep, ObjectStoreDep, SettingsDep, TenantSessionDep
from coursellm.api.schemas.document import (
    DocumentDetailResponse,
    DocumentIngestionResponse,
    DocumentResponse,
)
from coursellm.db.models.content import Document, DocumentStatus, SourceType
from coursellm.services.ingestion import (
    DocumentIngestionResult,
    create_document,
    delete_document,
    get_document_detail,
    list_documents,
)

router = APIRouter(prefix="/documents", tags=["documents"])


def _document_response(document: Document) -> DocumentResponse:
    return DocumentResponse.model_validate(document)


def _ingestion_response(result: DocumentIngestionResult) -> DocumentIngestionResponse:
    return DocumentIngestionResponse(
        **_document_response(result.document).model_dump(),
        chunks_processed=result.chunks_processed,
        reused=result.reused,
        degraded=list(result.degraded),
    )


@router.post(
    "",
    response_model=DocumentIngestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document into a course",
    description=(
        "Accepts a multipart upload, validates the extension, size and leading "
        "bytes, stores it under a server-generated key, then parses, chunks, "
        "embeds and indexes it inline in the request.\n\n"
        "**Course resolution.** Supply `course_id` for a course that already "
        "exists, or `course_name` to resolve it by name. A `course_name` the "
        "caller does not have yet is **created**, exactly as `POST /courses` "
        "does, and its id is returned in `course_id`. A `course_id` belonging to "
        "another tenant or another user is a 404 and nothing is stored.\n\n"
        "**Deduplication.** Identical bytes in the same course return the "
        "existing document with `200` and `reused=true`; pass `reingest=true` to "
        "force the pipeline to replace its chunks instead."
    ),
)
async def upload_document(
    context: ContextDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    store: ObjectStoreDep,
    response: Response,
    file: Annotated[UploadFile, File(description="The document to ingest.")],
    course_id: Annotated[
        uuid.UUID | None, Form(description="An existing course owned by the caller.")
    ] = None,
    course_name: Annotated[
        str | None, Form(description="Course name; created for the caller if absent.")
    ] = None,
    source_type: Annotated[
        SourceType | None,
        Form(description="Provenance label; inferred from the filename if omitted."),
    ] = None,
    reingest: Annotated[
        bool, Form(description="Replace the chunks of an existing identical document.")
    ] = False,
) -> DocumentIngestionResponse:
    # Bounded so a chunked request without a trustworthy Content-Length still
    # reaches the service's size check with at most one byte over the limit,
    # rather than buffering an unbounded body.
    data = await file.read(settings.max_upload_bytes + 1)
    result = await create_document(
        session,
        context,
        store,
        settings,
        user_id=context.user_id,
        course_id=course_id,
        course_name=course_name,
        filename=file.filename or "upload",
        content_type=file.content_type,
        data=data,
        source_type=source_type,
        reingest=reingest,
    )
    if not result.created:
        # An existing document was reused or re-ingested: this is not a new
        # resource, so it is not a 201.
        response.status_code = status.HTTP_200_OK
    return _ingestion_response(result)


@router.get(
    "",
    response_model=list[DocumentResponse],
    summary="List the caller's documents",
    description="Newest first, optionally filtered by course and ingestion status.",
)
async def list_documents_route(
    context: ContextDep,
    session: TenantSessionDep,
    course_id: Annotated[uuid.UUID | None, Query(description="Only this course.")] = None,
    document_status: Annotated[
        DocumentStatus | None, Query(alias="status", description="Only this ingestion status.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> list[DocumentResponse]:
    rows = await list_documents(
        session,
        context,
        user_id=context.user_id,
        course_id=course_id,
        document_status=document_status,
        limit=limit,
        offset=offset,
    )
    return [_document_response(row) for row in rows]


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="One document",
    description=(
        "A document belonging to another tenant or another user is reported as "
        "not found. Reporting it as forbidden would confirm that it exists."
    ),
)
async def get_document_route(
    document_id: uuid.UUID,
    context: ContextDep,
    session: TenantSessionDep,
) -> DocumentDetailResponse:
    detail = await get_document_detail(
        session, context, user_id=context.user_id, document_id=document_id
    )
    return DocumentDetailResponse(
        **_document_response(detail.document).model_dump(),
        chunk_count=detail.chunk_count,
        injection_classes=list(detail.document.injection_classes),
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its stored object",
    description=(
        "Removes the document row, cascading to chunks, embeddings, terms and the "
        "lexical statistics, then removes the stored object. If the object cannot "
        "be removed the request still succeeds: an orphaned blob is a cleanup job."
    ),
)
async def delete_document_route(
    document_id: uuid.UUID,
    context: ContextDep,
    session: TenantSessionDep,
    store: ObjectStoreDep,
) -> None:
    await delete_document(session, context, store, user_id=context.user_id, document_id=document_id)
