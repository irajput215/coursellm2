"""Upload validation: which error, and in which order.

The order is part of the contract. A cheap check that answers "should this
request be considered at all" must run before a check that inspects bytes, so
that an unsupported or oversized upload is rejected without the cost — and
without the confusing error — of a parser that was never going to accept it.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from coursellm.db.tenancy import TenantScope
from coursellm.services.ingestion import create_document, validate_upload
from coursellm.storage.base import ObjectNotFoundError

pytestmark = pytest.mark.unit

_TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")
_USER = uuid.UUID("44444444-4444-4444-4444-444444444444")
_COURSE = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class _RecordingStore:
    """An ObjectStore that records writes and otherwise does nothing."""

    backend = "recording"

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str | None]] = []

    async def put(self, key: str, data: bytes, *, content_type: str | None) -> None:
        self.puts.append((key, data, content_type))

    async def get(self, key: str) -> bytes:
        raise ObjectNotFoundError(key)

    async def delete(self, key: str) -> bool:
        return False

    async def exists(self, key: str) -> bool:
        return False


async def _create(settings: Settings, store: _RecordingStore, *, filename: str, data: bytes):
    # Validation precedes every database and storage interaction, so a ``None``
    # session is safe for the rejection cases these tests exercise: the function
    # raises before it could be used.
    return await create_document(
        cast("AsyncSession", None),
        TenantScope(_TENANT),
        store,
        settings,
        user_id=_USER,
        course_id=_COURSE,
        filename=filename,
        content_type="application/octet-stream",
        data=data,
    )


class TestRejections:
    async def test_disallowed_extension_is_unsupported_media_type(self) -> None:
        store = _RecordingStore()
        with pytest.raises(UnsupportedMediaTypeError) as caught:
            await _create(_settings(), store, filename="data.csv", data=b"a,b,c")
        assert caught.value.status_code == 415
        assert store.puts == []

    async def test_oversize_payload_is_reported_as_too_large(self) -> None:
        store = _RecordingStore()
        settings = _settings(max_upload_bytes=1024)
        with pytest.raises(PayloadTooLargeError) as caught:
            await _create(settings, store, filename="big.txt", data=b"x" * 2048)
        assert caught.value.status_code == 413
        assert store.puts == []

    async def test_pdf_without_the_pdf_header_is_rejected(self) -> None:
        store = _RecordingStore()
        with pytest.raises(UnsupportedMediaTypeError) as caught:
            await _create(_settings(), store, filename="paper.pdf", data=b"this is not a pdf")
        assert caught.value.status_code == 415
        assert store.puts == []

    async def test_docx_that_is_not_a_zip_is_rejected(self) -> None:
        store = _RecordingStore()
        with pytest.raises(UnsupportedMediaTypeError) as caught:
            await _create(_settings(), store, filename="thesis.docx", data=b"this is not a zip")
        assert caught.value.status_code == 415
        assert store.puts == []

    async def test_missing_extension_is_rejected(self) -> None:
        store = _RecordingStore()
        with pytest.raises(UnsupportedMediaTypeError):
            await _create(_settings(), store, filename="noextension", data=b"hello")
        assert store.puts == []


class TestValidationOrder:
    async def test_size_is_checked_before_the_content_sniff(self) -> None:
        # A ``.pdf`` that is both oversized *and* missing its header reports the
        # size, because the size comparison is the cheaper check.
        store = _RecordingStore()
        settings = _settings(max_upload_bytes=1024)
        with pytest.raises(PayloadTooLargeError) as caught:
            await _create(settings, store, filename="big.pdf", data=b"X" * 2048)
        assert caught.value.status_code == 413
        assert store.puts == []

    async def test_extension_is_checked_before_size(self) -> None:
        # A disallowed extension is reported even when the payload is also over
        # the limit: the extension check is a string comparison.
        store = _RecordingStore()
        settings = _settings(max_upload_bytes=1024, allowed_upload_extensions=".txt")
        with pytest.raises(UnsupportedMediaTypeError) as caught:
            await _create(settings, store, filename="big.pdf", data=b"x" * 2048)
        assert caught.value.status_code == 415
        assert store.puts == []


class TestAcceptedInputs:
    def test_plain_text_passes_and_returns_its_extension(self) -> None:
        assert validate_upload(_settings(), filename="notes.txt", data=b"hello") == ".txt"

    def test_markdown_passes(self) -> None:
        assert validate_upload(_settings(), filename="README.md", data=b"# hi") == ".md"

    def test_docx_zip_magic_passes(self) -> None:
        assert (
            validate_upload(_settings(), filename="thesis.docx", data=b"PK\x03\x04rest") == ".docx"
        )

    def test_pdf_header_passes(self) -> None:
        assert validate_upload(_settings(), filename="paper.pdf", data=b"%PDF-1.7\n...") == ".pdf"

    def test_extension_comparison_is_case_insensitive(self) -> None:
        assert validate_upload(_settings(), filename="PAPER.PDF", data=b"%PDF-1.4") == ".pdf"

    def test_a_file_exactly_at_the_limit_is_accepted(self) -> None:
        settings = _settings(max_upload_bytes=1024)
        assert validate_upload(settings, filename="exact.txt", data=b"x" * 1024) == ".txt"
