"""Log redaction tests.

Redaction is a control, not a convention. Each test below is a value that must
never appear in a log line, trace or error report.
"""

from __future__ import annotations

import pytest

from coursellm.core.logging import _scrub_text, redact_event

pytestmark = pytest.mark.unit


def _redact(event: dict) -> dict:
    return dict(redact_event(None, "info", dict(event)))


class TestFieldRedaction:
    @pytest.mark.parametrize(
        "field",
        [
            "password",
            "hashed_password",
            "secret",
            "secret_key",
            "token",
            "access_token",
            "api_key",
            "authorization",
            "cookie",
            "database_url",
            "content",
            "chunk_content",
            "prompt",
            "messages",
        ],
    )
    def test_sensitive_fields_are_replaced(self, field: str) -> None:
        assert _redact({field: "super-secret-value"})[field] == "[redacted]"

    def test_field_matching_is_case_insensitive(self) -> None:
        assert _redact({"API_KEY": "abc"})["API_KEY"] == "[redacted]"
        assert _redact({"Database_URL": "abc"})["Database_URL"] == "[redacted]"

    def test_safe_fields_survive(self) -> None:
        event = {"request_id": "abc123", "status_code": 200, "duration_ms": 12.5}
        assert _redact(event) == event


class TestValueScrubbing:
    """A secret can hide inside a field whose name looks harmless.

    Every credential below is fabricated at runtime from fragments rather than
    written as a literal. That keeps the secret scanner strict — it needs no
    exceptions for this file — and avoids teaching reviewers to ignore scanner
    hits in test code. The patterns exercised are the real ones.
    """

    def test_openai_key_in_free_text(self) -> None:
        key = "sk-" + "a" * 32
        scrubbed = _scrub_text(f"failed with key {key}")
        assert key not in scrubbed
        assert "[redacted]" in scrubbed

    def test_github_token_in_free_text(self) -> None:
        token = "gho_" + "A" * 30
        assert token not in _scrub_text(f"auth header was {token}")

    def test_aws_access_key_id(self) -> None:
        # AWS's documented example key prefix, not a real credential.
        key = "AKIA" + "IOSFODNN7EXAMPLE"
        assert key not in _scrub_text(f"creds {key} here")

    def test_jwt_is_scrubbed(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        assert jwt not in _scrub_text(f"bearer {jwt}")

    def test_password_inside_a_connection_string(self) -> None:
        password = "hunter" + "2"
        # Concatenated rather than interpolated into one literal, so that the
        # scanner sees no `scheme://user:password@` string anywhere in the file
        # and therefore needs no exception for it.
        scheme = "postgresql://"
        credentials = f"admin:{password}"
        dsn = scheme + credentials + "@db.internal:5432/prod"

        scrubbed = _scrub_text(dsn)

        assert password not in scrubbed
        assert scrubbed == scheme + "[redacted]" + "@db.internal:5432/prod"

    def test_value_scrubbing_applies_to_arbitrary_fields(self) -> None:
        """A key can be logged under a field name we did not anticipate."""
        event = _redact({"upstream_response": "invalid key sk-" + "a" * 32})
        assert "sk-" + "a" * 32 not in event["upstream_response"]

    def test_ordinary_text_is_untouched(self) -> None:
        text = "retrieved 20 candidates, reranked to 5, latency 412ms"
        assert _scrub_text(text) == text


class TestRedactionIsNotLossy:
    def test_non_string_values_pass_through(self) -> None:
        event = _redact({"count": 42, "ratio": 0.5, "flag": True, "items": [1, 2]})
        assert event == {"count": 42, "ratio": 0.5, "flag": True, "items": [1, 2]}

    def test_redaction_does_not_mutate_caller_dict_semantics(self) -> None:
        original = {"api_key": "x", "path": "/healthz"}
        result = redact_event(None, "info", original)
        assert result["path"] == "/healthz"
        assert result["api_key"] == "[redacted]"
