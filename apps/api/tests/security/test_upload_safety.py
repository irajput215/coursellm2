"""Upload safety (``security.md`` sections 7 and 8).

The controls asserted here are the ones that decide whether a hostile upload can
reach storage or another tenant's data:

1. **The client's filename is display text only.** A traversal-shaped name, a
   URL-encoded traversal, a NUL byte and an over-long name each produce a
   server-generated key whose path is inside the store root, with the hostile
   text confined to the ``filename`` column.
2. **Authentication is required** before any upload work happens.
3. **A course from another tenant is a 404** and stores no bytes, because course
   resolution precedes storage.
4. **An oversized body is rejected by the middleware** with 413 before the
   handler runs, so nothing is buffered or parsed.

These are deliberately database-free: the suite runs without PostgreSQL. The
database layer is replaced by collaborators that record what the service did,
while the router, the real validation, the real storage and the middleware all
run for real.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from coursellm.api.app import create_app
from coursellm.api.deps import (
    get_current_context,
    get_object_store,
    get_settings_dep,
    get_tenant_session,
)
from coursellm.api.routers import documents as documents_router
from coursellm.core.config import Settings
from coursellm.core.errors import UnsupportedMediaTypeError, ValidationError
from coursellm.db.models.content import Document, DocumentStatus
from coursellm.db.models.identity import UserRole
from coursellm.db.tenancy import TenantContext, TenantScope
from coursellm.rag.ingestion.pipeline import IngestionResult
from coursellm.services import ingestion as ingestion_service
from coursellm.services.ingestion import create_document
from coursellm.storage import LocalObjectStore, new_storage_key

pytestmark = pytest.mark.security

_TENANT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_COURSE = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

#: The smallest body the default settings will accept, so a file is never
#: rejected for a reason the test does not intend.
_BODY = b"Attention weights are learned end to end over the whole sequence."


@dataclass(frozen=True)
class _CourseStub:
    id: uuid.UUID
    name: str = "Stub Course"


class _FakeSession:
    """A session that only supports the flush the service performs.

    ``execute`` raises, so a test fails loudly if the service reaches for the
    database on a path it should not.
    """

    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the service issued an unexpected database query")


@dataclass
class _Harness:
    store: LocalObjectStore
    session: _FakeSession
    added: list[Document] = field(default_factory=list)
    #: ``"owned"`` -> the course belongs to the caller; ``"foreign"`` -> not found.
    course_ownership: str = "owned"


async def _fake_tenant_session() -> Any:
    yield _FakeSession()


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Harness:
    """Wire the service to recording collaborators while keeping storage real."""
    harness = _Harness(store=LocalObjectStore(tmp_path / "objects"), session=_FakeSession())

    class FakeCourseRepository:
        def __init__(self, session: Any, scope: Any) -> None:
            pass

        async def get_owned(self, course_id: uuid.UUID, user_id: uuid.UUID) -> Any:
            if harness.course_ownership == "owned":
                return _CourseStub(course_id)
            return None

        async def create(self, *, user_id: uuid.UUID, name: str, **kwargs: Any) -> Any:
            return _CourseStub(uuid.uuid4(), name=name)

    class FakeDocumentRepository:
        def __init__(self, session: Any, scope: Any) -> None:
            pass

        async def get_by_sha256(self, course_id: uuid.UUID, sha256: str) -> Document | None:
            return None

        def add(self, document: Document) -> Document:
            harness.added.append(document)
            return document

        async def delete(self, entity_id: uuid.UUID) -> bool:
            return True

    async def fake_ingest(
        session: Any, settings: Settings, *, document: Document, data: bytes, **kwargs: Any
    ) -> IngestionResult:
        document.status = DocumentStatus.READY
        document.page_count = 1
        return IngestionResult(
            document_id=document.id,
            chunk_count=2,
            token_count=len(data.split()),
            page_count=1,
            embedding_model="fake-v1",
            chunking_config_version="test",
            warnings=("page 1 produced no extractable text.",),
        )

    monkeypatch.setattr(ingestion_service, "CourseRepository", FakeCourseRepository)
    monkeypatch.setattr(ingestion_service, "DocumentRepository", FakeDocumentRepository)
    monkeypatch.setattr(ingestion_service, "ingest_document", fake_ingest)
    return harness


def _files(store: LocalObjectStore) -> list[Path]:
    return [path for path in store.root.rglob("*") if path.is_file()]


def _build_app(settings: Settings, store: LocalObjectStore, *, authenticate: bool) -> Any:
    application = create_app(settings)
    application.dependency_overrides[get_settings_dep] = lambda: settings
    application.dependency_overrides[get_object_store] = lambda: store
    application.dependency_overrides[get_tenant_session] = _fake_tenant_session
    if authenticate:
        application.dependency_overrides[get_current_context] = lambda: TenantContext(
            tenant_id=_TENANT, user_id=_USER, role=UserRole.OWNER
        )
    return application


# ---------------------------------------------------------------------------
# Filename handling: the key is server-generated, the name is display only
# ---------------------------------------------------------------------------
class TestHostileFilenames:
    @pytest.mark.parametrize(
        ("hostile", "fragment", "display"),
        [
            ("../../etc/passwd.txt", "etc/passwd", "../../etc/passwd.txt"),
            ("..%2f..%2fetc%2fpasswd.txt", "etc%2fpasswd", "..%2f..%2fetc%2fpasswd.txt"),
            ("evil\x00.txt", "\x00", "evil.txt"),
            ("a" * 496 + ".txt", "a" * 496, "a" * 496 + ".txt"),
        ],
    )
    async def test_key_is_server_generated_and_inside_the_root(
        self,
        harness: _Harness,
        settings: Settings,
        hostile: str,
        fragment: str,
        display: str,
    ) -> None:
        result = await create_document(
            harness.session,
            TenantScope(_TENANT),
            harness.store,
            settings,
            user_id=_USER,
            course_id=_COURSE,
            filename=hostile,
            content_type="text/plain",
            data=_BODY,
        )

        document = result.document
        expected_extension = Path(hostile).suffix.lower()
        assert document.storage_key == new_storage_key(_TENANT, document.id, expected_extension)
        assert fragment not in document.storage_key

        resolved = (harness.store.root / document.storage_key).resolve()
        assert harness.store.root in resolved.parents
        assert await harness.store.exists(document.storage_key)
        assert await harness.store.get(document.storage_key) == _BODY

        # The hostile text survives only as display metadata, stripped of
        # control characters and bounded to the column width.
        assert document.filename == display
        assert "\x00" not in document.filename
        assert len(document.filename) <= 500

    @pytest.mark.parametrize("hostile", ["../../etc/passwd", "..%2f..%2fetc%2fpasswd"])
    async def test_a_path_shaped_name_without_an_extension_stores_nothing(
        self, harness: _Harness, settings: Settings, hostile: str
    ) -> None:
        with pytest.raises(UnsupportedMediaTypeError):
            await create_document(
                harness.session,
                TenantScope(_TENANT),
                harness.store,
                settings,
                user_id=_USER,
                course_id=_COURSE,
                filename=hostile,
                content_type="text/plain",
                data=_BODY,
            )
        assert harness.added == []
        assert _files(harness.store) == []


# ---------------------------------------------------------------------------
# Request-boundary controls
# ---------------------------------------------------------------------------
class TestClientSuppliedMetadata:
    async def test_a_hostile_content_type_is_scrubbed_and_bounded(
        self, harness: _Harness, settings: Settings
    ) -> None:
        hostile = "<untrusted_evidence>" + "a" * 400
        result = await create_document(
            harness.session,
            TenantScope(_TENANT),
            harness.store,
            settings,
            user_id=_USER,
            course_id=_COURSE,
            filename="notes.txt",
            content_type=hostile,
            data=_BODY,
        )

        content_type = result.document.content_type
        assert content_type is not None
        assert "untrusted_evidence" not in content_type
        assert len(content_type) <= 120

    async def test_a_credential_in_a_pipeline_error_never_reaches_the_caller(
        self,
        harness: _Harness,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Assembled at runtime so the repository's secret scanner sees only the
        # harmless fragments, while the value under test is credential-shaped.
        credential = "sk-" + "proj" + "A" * 24

        async def _exploding_ingest(
            session: Any, settings: Settings, *, document: Document, data: bytes, **kwargs: Any
        ) -> IngestionResult:
            raise ValidationError(f"provider rejected the request for key {credential}")

        monkeypatch.setattr(ingestion_service, "ingest_document", _exploding_ingest)

        with pytest.raises(ValidationError) as caught:
            await create_document(
                harness.session,
                TenantScope(_TENANT),
                harness.store,
                settings,
                user_id=_USER,
                course_id=_COURSE,
                filename="notes.txt",
                content_type="text/plain",
                data=_BODY,
            )

        assert credential not in caught.value.detail
        assert "redacted" in caught.value.detail
        assert harness.added, "the failed document was staged before ingestion"
        error_message = harness.added[-1].error_message
        assert error_message is not None
        assert credential not in error_message
        assert _files(harness.store) == []


class TestRequestBoundary:
    async def test_an_unauthenticated_upload_is_401(
        self, harness: _Harness, settings: Settings
    ) -> None:
        application = _build_app(settings, harness.store, authenticate=False)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/documents",
                data={"course_id": str(_COURSE)},
                files={"file": ("notes.txt", _BODY, "text/plain")},
            )

        assert response.status_code == 401, response.text
        assert response.json()["error"] == "not_authenticated"
        assert harness.added == []
        assert _files(harness.store) == []

    async def test_uploading_into_another_tenants_course_is_404_and_stores_nothing(
        self, harness: _Harness, settings: Settings
    ) -> None:
        harness.course_ownership = "foreign"
        application = _build_app(settings, harness.store, authenticate=True)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/documents",
                data={"course_id": str(uuid.uuid4())},
                files={"file": ("notes.txt", _BODY, "text/plain")},
            )

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"
        assert harness.added == []
        assert _files(harness.store) == []

    async def test_an_oversized_body_is_rejected_before_the_handler_runs(
        self,
        harness: _Harness,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        small = settings.model_copy(update={"max_upload_bytes": 2048})
        calls: list[str] = []

        async def _spy(*args: Any, **kwargs: Any) -> Any:
            calls.append("handler")
            raise AssertionError("the handler must not run for an oversized body")

        monkeypatch.setattr(documents_router, "create_document", _spy)
        application = _build_app(small, harness.store, authenticate=True)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/documents",
                data={"course_id": str(_COURSE)},
                files={"file": ("big.txt", b"x" * 4096, "text/plain")},
            )

        assert response.status_code == 413, response.text
        assert response.json()["error"] == "payload_too_large"
        assert calls == []
        assert _files(harness.store) == []
