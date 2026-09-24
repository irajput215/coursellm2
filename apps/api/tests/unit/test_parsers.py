"""Parser tests.

The property under test is not "does this file parse" but "can a failure ever
look like success". Every malformed input must raise, and every page that
extracts to nothing must be reported as a warning rather than dropped.
"""

from __future__ import annotations

import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from coursellm.db.models.content import SourceType
from coursellm.rag.ingestion.parsers import parse_document, sniff_source_type

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class TestPlainText:
    def test_txt_parses_as_one_page(self) -> None:
        parsed = parse_document(
            b"Linear algebra notes.", filename="notes.txt", content_type="text/plain"
        )

        assert parsed.page_count == 1
        assert len(parsed.pages) == 1
        assert parsed.pages[0].page == 1
        assert "Linear algebra" in parsed.pages[0].text
        assert parsed.warnings == []

    def test_markdown_parses_as_one_page(self) -> None:
        parsed = parse_document(
            b"# Heading\n\nBody text.", filename="README.md", content_type="text/markdown"
        )

        assert parsed.page_count == 1
        assert "# Heading" in parsed.pages[0].text

    def test_empty_text_is_a_warning_not_an_empty_document(self) -> None:
        parsed = parse_document(b"   \n\t", filename="empty.txt", content_type="text/plain")

        assert parsed.page_count == 1
        assert parsed.pages[0].text.strip() == ""
        assert parsed.warnings  # reported, not silently swallowed

    def test_non_utf8_text_raises(self) -> None:
        with pytest.raises(ValidationError):
            parse_document(b"\xff\xfe\x00bad", filename="bad.txt", content_type="text/plain")


class TestRejections:
    def test_unsupported_extension_raises(self) -> None:
        with pytest.raises(UnsupportedMediaTypeError):
            parse_document(b"a,b,c", filename="data.csv", content_type="text/csv")

    def test_missing_extension_raises(self) -> None:
        with pytest.raises(UnsupportedMediaTypeError):
            parse_document(b"data", filename="noextension", content_type=None)

    def test_allowed_but_unimplemented_extension_raises(self) -> None:
        # ``.pptx`` is in the default allowed list but has no parser yet; the
        # failure must be explicit rather than an empty document.
        with pytest.raises(UnsupportedMediaTypeError):
            parse_document(b"PK\x03\x04", filename="slides.pptx", content_type=None)

    def test_oversize_upload_raises(self) -> None:
        settings = _settings(max_upload_bytes=1024)
        with pytest.raises(PayloadTooLargeError):
            parse_document(
                b"x" * 2048, filename="big.txt", content_type="text/plain", settings=settings
            )

    def test_explicit_settings_control_allowed_extensions(self) -> None:
        settings = _settings(allowed_upload_extensions=".txt")
        with pytest.raises(UnsupportedMediaTypeError):
            parse_document(b"%PDF-1.4", filename="paper.pdf", content_type=None, settings=settings)


class TestPdf:
    def test_malformed_pdf_raises_rather_than_returning_empty(self) -> None:
        with pytest.raises(ValidationError):
            parse_document(
                b"this is not a pdf", filename="broken.pdf", content_type="application/pdf"
            )

    def test_empty_pdf_bytes_raise(self) -> None:
        with pytest.raises(ValidationError):
            parse_document(b"", filename="nothing.pdf", content_type="application/pdf")


class TestSourceTypeSniffing:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("lecture-4.pdf", SourceType.LECTURE),
            ("lecture-slides.pdf", SourceType.SLIDE),
            ("week3_slides.pptx", SourceType.SLIDE),
            ("arxiv-1706.03762.pdf", SourceType.PAPER),
            ("deep-learning-book.pdf", SourceType.BOOK),
            ("cs229-syllabus.pdf", SourceType.SYLLABUS),
            ("my-notes.txt", SourceType.NOTES),
            ("api-documentation.md", SourceType.DOCUMENTATION),
            ("random-upload.pdf", SourceType.OTHER),
        ],
    )
    def test_heuristics(self, filename: str, expected: SourceType) -> None:
        assert sniff_source_type(filename) == expected
