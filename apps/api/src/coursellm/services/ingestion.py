"""Document ingestion use case: validate, store, persist, ingest.

This is the layer that turns an HTTP upload into retrievable chunks. It sits
between the documents router (transport) and the ingestion pipeline (parsing,
chunking, embedding, statistics), and it owns the rules that span all three:
what is accepted, what is deduplicated, what happens when ingestion fails, and
what the stored object's lifecycle is.

## Validation order

Extension, then size, then a cheap content sniff. The order is a contract, not
an implementation detail, because it decides which error a caller sees:
``.csv``-shaped input is unsupported (415) even if it is also huge, and an
oversized ``.pdf`` is reported as too large (413) rather than being handed to a
PDF parser that would fail on the first page. The sniff is the last check
because it is the only one that inspects content, and it exists so a ``.pdf``
whose bytes are not a PDF is rejected at the boundary instead of producing a
confusing parser error (or, worse, being stored and failing later).

## Storage before the row

The bytes are written under a server-generated key *before* the ``documents``
row is inserted, and both are removed if any later step fails. The prototype
committed the document row first and embedded afterwards, so a failed ingest
left a visible document and a stray file whose retry would fail on the
unique ``(tenant, course, sha256)`` key. Nothing here commits a partial result:
the caller owns the transaction boundary, and this service also deletes the
stored object on the failure path so no blob outlives its row even if a caller
chooses to commit after catching.

## Inline, not background

``ingest_document`` runs inline in the request. A background task would need a
durable queue to be correct — an in-process ``BackgroundTasks`` callback is lost
on restart, leaving the document at ``pending`` forever with no worker to pick
it up, which is strictly worse than a slow request — and the project has no
worker module yet (``make worker`` names one that does not exist). The bounded
``max_upload_bytes`` (25 MiB by default) keeps the worst-case request finite, so
a synchronous ingest is honest about its cost. A worker with a durable queue is
the follow-up; the ``documents.status`` column already models the
``pending → parsing → embedding → indexing → ready|failed`` states such a worker
would need, so introducing one is a deployment change rather than a schema one.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.errors import (
    CourseLLMError,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
    UpstreamError,
)
from coursellm.core.logging import get_logger
from coursellm.db.models.content import (
    Chunk,
    Course,
    Document,
    DocumentStatus,
    SourceType,
)
from coursellm.db.tenancy import TenantScope
from coursellm.rag.ingestion.parsers import sniff_source_type
from coursellm.rag.ingestion.pipeline import (
    IngestionResult,
    ingest_document,
)
from coursellm.rag.ingestion.pipeline import (
    reingest as reingest_document,
)
from coursellm.repositories.content import CourseRepository, DocumentRepository
from coursellm.security.output import redact_secrets
from coursellm.security.sanitize import scrub_for_storage
from coursellm.storage.base import ObjectStore, ObjectStoreError, new_storage_key

logger = get_logger(__name__)

#: The filename column is ``String(500)``; a display name is truncated to it.
_MAX_FILENAME_CHARS = 500

#: The ``content_type`` column is ``String(120)``. The value is client-supplied
#: (the multipart part's ``Content-Type``), so it is both scrubbed and bounded.
_MAX_CONTENT_TYPE_CHARS = 120

#: ``documents.error_message`` is free text surfaced to the uploader; it is
#: bounded so a pathological exception cannot bloat the row or a response.
_MAX_ERROR_MESSAGE_CHARS = 500

#: What a user is told when the failure was not a typed domain error. Never the
#: exception's own text: it may name a provider, a host or an internal class.
_GENERIC_FAILURE_MESSAGE = (
    "The document could not be processed and was not added. "
    "Check that the file opens correctly, then try again."
)

#: Magic prefixes per extension that the content sniff checks. A DOCX is a zip,
#: so all three zip local-header signatures count; a PDF is identified by its
#: header. ``.txt``/``.md`` are text by definition and are not sniffed.
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


@dataclass(frozen=True, slots=True)
class DocumentIngestionResult:
    """The outcome of one upload, for the router to render."""

    document: Document
    created: bool
    reused: bool
    chunks_processed: int
    page_count: int | None
    degraded: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    """A document together with the chunk count the detail view reports."""

    document: Document
    chunk_count: int


def validate_upload(settings: Settings, *, filename: str, data: bytes) -> str:
    """Validate an upload before any bytes are stored, and return its extension.

    Order: extension, then size, then content sniff. See the module docstring
    for why the order is part of the contract.
    """
    extension = Path(filename).suffix.lower()
    if not extension:
        raise UnsupportedMediaTypeError(
            "The file has no extension; cannot determine how to parse it."
        )
    if extension not in settings.allowed_extensions:
        allowed = ", ".join(sorted(settings.allowed_extensions))
        # The extension is echoed, not the whole filename: it is short, it is
        # the reason for the rejection, and a caller-supplied path or megabyte
        # of name has no business in a response body.
        raise UnsupportedMediaTypeError(
            f"{extension[:32]!r} files are not accepted (allowed: {allowed})."
        )

    # Checked before the sniff so an oversized file is reported as oversized
    # even when its content is also wrong, and so the (cheap) size comparison
    # is never skipped in favour of inspecting bytes.
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"File is {len(data)} bytes; the limit is {settings.max_upload_bytes} bytes."
        )

    _sniff_content(extension, data)
    return extension


def _sniff_content(extension: str, data: bytes) -> None:
    """Reject bytes that clearly do not match the declared extension.

    This is not a full format validation — the parser does that. It is a
    one-comparison guard against the common case of a mislabelled file, so the
    error is "this is not a PDF" rather than a parser traceback.
    """
    if extension == ".pdf" and not data.startswith(_PDF_MAGIC):
        raise UnsupportedMediaTypeError(
            "The file has a .pdf name but its content is not a PDF. Re-export it and upload again."
        )
    if extension == ".docx" and data[:4] not in _ZIP_MAGIC:
        raise UnsupportedMediaTypeError(
            "The file has a .docx name but its content is not a DOCX (zip) archive. "
            "Re-export it and upload again."
        )


async def create_document(
    session: AsyncSession,
    scope: TenantScope,
    store: ObjectStore,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    filename: str,
    content_type: str | None,
    data: bytes,
    course_id: uuid.UUID | None = None,
    course_name: str | None = None,
    source_type: SourceType | None = None,
    reingest: bool = False,
) -> DocumentIngestionResult:
    """Store and ingest one uploaded document.

    ``course_id`` and ``course_name`` are alternatives. An explicit id must
    belong to the caller, or the request is a 404 before any byte is stored. A
    name that does not yet exist is created for the caller, matching
    ``POST /courses``; that is stated in the endpoint description rather than
    done silently, because a typo otherwise produces a new empty course.

    Identical bytes in the same course are the same document: the existing row
    is returned with ``reused=True`` unless ``reingest`` is set, in which case
    the pipeline's re-ingest path replaces its chunks.
    """
    extension = validate_upload(settings, filename=filename, data=data)
    display_name = _display_filename(filename)
    safe_content_type = _safe_content_type(content_type)

    # Course resolution precedes storage: an upload into another tenant's (or
    # another user's) course must store nothing at all.
    course = await _resolve_course(
        session, scope, user_id=user_id, course_id=course_id, course_name=course_name
    )

    sha256 = hashlib.sha256(data).hexdigest()
    documents = DocumentRepository(session, scope)
    existing = await documents.get_by_sha256(course.id, sha256)

    if existing is not None and not reingest:
        # A second copy would double every retrieval result for these bytes.
        return DocumentIngestionResult(
            document=existing,
            created=False,
            reused=True,
            chunks_processed=await _chunk_count(session, existing.id, scope.tenant_id),
            page_count=existing.page_count,
        )

    if existing is not None:
        return await _reingest_existing(
            session,
            scope,
            store,
            settings,
            document=existing,
            content_type=safe_content_type,
            data=data,
        )

    return await _create_and_ingest(
        session,
        scope,
        store,
        settings,
        user_id=user_id,
        course=course,
        extension=extension,
        display_name=display_name,
        content_type=safe_content_type,
        data=data,
        sha256=sha256,
        source_type=source_type if source_type is not None else sniff_source_type(filename),
    )


async def list_documents(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None = None,
    document_status: DocumentStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Document]:
    """The caller's documents, newest first.

    Scoped by tenant and by user. The user predicate is what makes another
    person's document in the same tenant indistinguishable from a missing one,
    and the tenant predicate keeps the statement correct even without RLS.
    """
    statement = select(Document).where(
        Document.tenant_id == scope.tenant_id,
        Document.user_id == user_id,
    )
    if course_id is not None:
        statement = statement.where(Document.course_id == course_id)
    if document_status is not None:
        statement = statement.where(Document.status == document_status)
    statement = (
        statement.order_by(Document.created_at.desc(), Document.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(statement)
    return list(result.scalars().all())


async def get_document_detail(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> DocumentDetail:
    """One of the caller's documents, with its chunk count.

    Cross-tenant and cross-user both surface as ``NotFoundError`` (404): a 403
    would confirm that the document exists.
    """
    document = await _owned_document(session, scope, user_id=user_id, document_id=document_id)
    return DocumentDetail(
        document=document,
        chunk_count=await _chunk_count(session, document.id, scope.tenant_id),
    )


async def delete_document(
    session: AsyncSession,
    scope: TenantScope,
    store: ObjectStore,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    """Delete a document, its children, and its stored object.

    The row goes first: the foreign keys cascade to chunks, embeddings and
    terms, so the database is consistent the moment the delete succeeds. The
    object is removed afterwards, and a failure there is logged rather than
    raised — an orphaned blob is a cleanup job, not a failed user action, and
    the user's document is already gone.

    ``tenant_lexical_stats`` and ``tenant_corpus_stats`` are *not* reached by
    that cascade: they are materialised aggregates keyed by tenant, with no
    foreign key to ``documents``. Their correctness is therefore maintained
    here, with the pipeline's own recompute routines imported deliberately
    rather than reimplemented, so the ingest and delete paths cannot drift.
    """
    document = await _owned_document(session, scope, user_id=user_id, document_id=document_id)
    storage_key = document.storage_key

    # Collected before the chunks disappear: they are the terms whose document
    # frequency may have changed.
    from coursellm.rag.ingestion.pipeline import (
        _recompute_corpus_stats,
        _recompute_lexical_stats,
        _terms_for_document,
    )

    affected_terms = await _terms_for_document(session, document.id, scope.tenant_id)

    await DocumentRepository(session, scope).delete(document.id)
    await session.flush()
    await _recompute_lexical_stats(session, tenant_id=scope.tenant_id, terms=affected_terms)
    await _recompute_corpus_stats(session, tenant_id=scope.tenant_id)

    try:
        await store.delete(storage_key)
    except ObjectStoreError:
        logger.warning(
            "document_object_delete_failed",
            document_id=str(document_id),
            storage_key=storage_key,
            backend=store.backend,
            impact="An orphaned object remains; a cleanup job should remove it.",
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _create_and_ingest(
    session: AsyncSession,
    scope: TenantScope,
    store: ObjectStore,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    course: Course,
    extension: str,
    display_name: str,
    content_type: str | None,
    data: bytes,
    sha256: str,
    source_type: SourceType,
) -> DocumentIngestionResult:
    # The id is generated before the row exists because the storage key is
    # derived from it: bytes are written first, under a key that names the row
    # they will belong to.
    document_id = uuid.uuid4()
    storage_key = new_storage_key(scope.tenant_id, document_id, extension)

    try:
        await store.put(storage_key, data, content_type=content_type)
    except ObjectStoreError as exc:
        logger.warning(
            "document_store_failed",
            document_id=str(document_id),
            backend=store.backend,
        )
        raise ServiceUnavailableError("The upload could not be stored. Please try again.") from exc

    document = Document(
        id=document_id,
        course_id=course.id,
        user_id=user_id,
        filename=display_name,
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=len(data),
        sha256=sha256,
        source_type=source_type,
        status=DocumentStatus.PENDING,
    )
    documents = DocumentRepository(session, scope)
    documents.add(document)
    try:
        await session.flush()
    except Exception:
        # No row was written, so the only cleanup is the object.
        await _discard_object(store, storage_key, document_id=document_id)
        raise

    try:
        result = await ingest_document(session, settings, document=document, data=data)
    except Exception as exc:
        await _handle_ingest_failure(
            session, scope, store, document=document, storage_key=storage_key, created=True, exc=exc
        )
    await session.flush()
    return _result(document, result, created=True, reused=False)


async def _reingest_existing(
    session: AsyncSession,
    scope: TenantScope,
    store: ObjectStore,
    settings: Settings,
    *,
    document: Document,
    content_type: str | None,
    data: bytes,
) -> DocumentIngestionResult:
    """Re-run the pipeline for a document whose bytes were uploaded again.

    The bytes are re-written under the existing key: the sha256 is unchanged,
    but the object may have been removed by a cleanup job, and re-ingest is
    exactly the moment to restore it.
    """
    storage_key = document.storage_key
    try:
        await store.put(storage_key, data, content_type=content_type)
    except ObjectStoreError as exc:
        logger.warning("document_store_failed", document_id=str(document.id), backend=store.backend)
        raise ServiceUnavailableError("The upload could not be stored. Please try again.") from exc

    try:
        result = await reingest_document(session, settings, document=document, data=data)
    except Exception as exc:
        # The row already existed, so it is kept and marked failed rather than
        # removed; only the stored object is cleaned up.
        await _handle_ingest_failure(
            session,
            scope,
            store,
            document=document,
            storage_key=storage_key,
            created=False,
            exc=exc,
        )
    await session.flush()
    return _result(document, result, created=False, reused=False)


def _result(
    document: Document, result: IngestionResult, *, created: bool, reused: bool
) -> DocumentIngestionResult:
    return DocumentIngestionResult(
        document=document,
        created=created,
        reused=reused,
        chunks_processed=result.chunk_count,
        page_count=result.page_count,
        degraded=tuple(result.warnings),
    )


async def _handle_ingest_failure(
    session: AsyncSession,
    scope: TenantScope,
    store: ObjectStore,
    *,
    document: Document,
    storage_key: str,
    created: bool,
    exc: Exception,
) -> NoReturn:
    """Record a safe failure, remove the object, and raise a domain error.

    ``status=FAILED`` with a bounded, credential-redacted message is written so
    that a caller which commits after catching has a durable failure record. For
    a document created by this request the row is *also* deleted, so a commit
    cannot leave a document with no bytes and no chunks; for a re-ingest the
    pre-existing row is kept. The transaction boundary still belongs to the
    caller, so in the normal raise-through path the rollback removes the row
    regardless — the explicit delete is what makes the no-orphan property hold
    even for a caller that swallows the exception.
    """
    message = _safe_failure_message(exc)
    document.status = DocumentStatus.FAILED
    document.error_message = message
    try:
        await session.flush()
    except Exception:
        # An aborted transaction (an IntegrityError does this) cannot record the
        # status until the caller rolls back; the real cause must still surface.
        logger.warning(
            "document_failure_status_not_persisted",
            document_id=str(document.id),
            exc_type=type(exc).__name__,
        )

    await _discard_object(store, storage_key, document_id=document.id)

    if created:
        try:
            await DocumentRepository(session, scope).delete(document.id)
            await session.flush()
        except Exception:
            logger.warning(
                "failed_document_row_not_removed",
                document_id=str(document.id),
                exc_type=type(exc).__name__,
            )

    raise _as_domain_error(exc, message) from exc


async def _discard_object(store: ObjectStore, storage_key: str, *, document_id: uuid.UUID) -> None:
    """Best-effort removal of an object; never masks the original failure."""
    try:
        await store.delete(storage_key)
    except ObjectStoreError:
        logger.warning(
            "document_object_cleanup_failed",
            document_id=str(document_id),
            storage_key=storage_key,
            backend=store.backend,
            impact="An orphaned object remains; a cleanup job should remove it.",
        )


async def _resolve_course(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    course_name: str | None,
) -> Course:
    courses = CourseRepository(session, scope)
    if course_id is not None:
        course = await courses.get_owned(course_id, user_id)
        if course is None:
            raise NotFoundError("Course not found.")
        return course

    name = (course_name or "").strip()
    if not name:
        raise NotFoundError("Course not found.")

    existing = await _course_by_name(session, scope, user_id=user_id, name=name)
    if existing is not None:
        return existing
    # Create-if-absent, mirroring POST /courses. The endpoint description says
    # so explicitly, so a mistyped name is a visible new course rather than a
    # silent one. The flush assigns the surrogate key the document row needs.
    course = await courses.create(user_id=user_id, name=name)
    await session.flush()
    return course


async def _course_by_name(
    session: AsyncSession, scope: TenantScope, *, user_id: uuid.UUID, name: str
) -> Course | None:
    """Look a course up by owner and name.

    Deliberately not ``CourseRepository.get_by_name``: that method filters by
    tenant only, and the uniqueness constraint is ``(tenant, user, name)``, so
    two users with the same course name would make it raise rather than return
    one row.
    """
    statement = select(Course).where(
        Course.tenant_id == scope.tenant_id,
        Course.user_id == user_id,
        Course.name == name,
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def _owned_document(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Document:
    document = await DocumentRepository(session, scope).get(document_id)
    if document is None or document.user_id != user_id:
        raise NotFoundError("Document not found.")
    return document


async def _chunk_count(session: AsyncSession, document_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
    statement = (
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == document_id, Chunk.tenant_id == tenant_id)
    )
    return int((await session.execute(statement)).scalar_one())


def _display_filename(filename: str) -> str:
    """A safe display name: markers and control characters removed, bounded.

    ``scrub_for_storage`` strips NUL bytes — PostgreSQL text cannot store them —
    as well as the prompt-injection markers and invisible characters that could
    make a rendered filename lie about itself. The value is never used to build
    a path; this is defence in depth for the display path.
    """
    cleaned = scrub_for_storage(filename).strip()
    if not cleaned:
        cleaned = "upload"
    return cleaned[:_MAX_FILENAME_CHARS]


def _safe_content_type(content_type: str | None) -> str | None:
    """A bounded, scrubbed copy of the client-declared media type.

    The value is the multipart part's ``Content-Type``, which the client
    controls, and it is persisted and returned. Scrubbing removes markers and
    control characters, and the column width is enforced here so a pathological
    header cannot become a database error.
    """
    if content_type is None:
        return None
    cleaned = scrub_for_storage(content_type).strip()
    return cleaned[:_MAX_CONTENT_TYPE_CHARS] or None


def _safe_failure_message(exc: BaseException) -> str:
    """A user-actionable failure message that leaks nothing internal.

    A typed domain error's ``detail`` is already authored for users. Anything
    else is replaced entirely, because its text may name a provider, a host or
    an internal class. The result is scrubbed and credential-redacted before it
    is persisted (and therefore before it can be rendered).
    """
    raw = exc.detail if isinstance(exc, CourseLLMError) else _GENERIC_FAILURE_MESSAGE
    cleaned = scrub_for_storage(raw)
    redacted, _kinds = redact_secrets(cleaned)
    message = redacted.strip()[:_MAX_ERROR_MESSAGE_CHARS]
    return message or _GENERIC_FAILURE_MESSAGE


def _as_domain_error(exc: BaseException, message: str) -> CourseLLMError:
    """Translate a failure into a domain error carrying the *safe* message.

    A typed domain error keeps its class, and therefore its status and error
    code, but its detail is replaced by the scrubbed, credential-redacted
    message. Re-raising the original would put a parser or provider string — the
    exact text ``_safe_failure_message`` exists to contain — into the response,
    even though the row got the safe copy.
    """
    if isinstance(exc, CourseLLMError):
        exc.detail = message
        return exc
    return UpstreamError(message)


__all__ = [
    "DocumentDetail",
    "DocumentIngestionResult",
    "create_document",
    "delete_document",
    "get_document_detail",
    "list_documents",
    "validate_upload",
]
