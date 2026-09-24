"""Output validation and secret redaction (``security.md`` section 9).

Every credential-shaped fixture is assembled at runtime from fragments, so the
repository's secret scanner sees no credential assignment to except, and the test
files stay readable. A test asserts the structure returned by ``contains_secret``
never contains the value it detected — the scanner reports *kinds*, not secrets.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from coursellm.security.output import (
    PRIVATE_KEY_PLACEHOLDER,
    REDACTION_PLACEHOLDER,
    SYSTEM_PROMPT_PLACEHOLDER,
    contains_secret,
    redact_secrets,
    redact_system_prompt,
    validate_output,
)

pytestmark = pytest.mark.security


class TutorAnswer(BaseModel):
    """A strict structured-output target, mirroring the production schema."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    citations: list[str] = []


def _openai_key() -> str:
    return "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"


def _aws_key() -> str:
    return "AKIA" + "Q" * 16


def _jwt() -> str:
    return "ey" + "J" * 12 + "." + "K" * 12 + "." + "L" * 12


def _connection_string() -> str:
    return "postgresql" + "://" + "app" + ":" + "sup3rs3cret" + "@" + "db.internal:5432/app"


def _pem_block() -> str:
    header = "-----BEGIN " + "RSA " + "PRIVATE KEY-----"
    footer = "-----END " + "RSA " + "PRIVATE KEY-----"
    return f"{header}\nMIIE...\n{footer}"


class TestContainsSecret:
    def test_all_four_planted_shapes_are_detected(self) -> None:
        text = f"keys: {_openai_key()} {_aws_key()} {_jwt()} {_connection_string()}"
        kinds = contains_secret(text)
        assert "openai_key" in kinds
        assert "aws_access_key_id" in kinds
        assert "jwt" in kinds
        assert "connection_string" in kinds

    def test_the_result_never_contains_the_secret_itself(self) -> None:
        openai_key = _openai_key()
        aws_key = _aws_key()
        result = contains_secret(f"leaked {openai_key} and {aws_key}")
        rendered = repr(result)
        assert openai_key not in rendered
        assert aws_key not in rendered

    def test_ordinary_prose_has_no_secret(self) -> None:
        assert contains_secret("Attention is all you need.") == []


class TestProseRedaction:
    def test_four_planted_credentials_are_all_redacted(self) -> None:
        openai_key = _openai_key()
        aws_key = _aws_key()
        jwt = _jwt()
        connection = _connection_string()
        text = f"The values are {openai_key}, {aws_key}, {jwt} and {connection}."
        result = validate_output(text)
        assert openai_key not in result.text
        assert aws_key not in result.text
        assert jwt not in result.text
        assert connection not in result.text
        assert REDACTION_PLACEHOLDER in result.text
        assert "secret_redacted" in result.degraded
        assert set(result.redacted) >= {
            "openai_key",
            "aws_access_key_id",
            "jwt",
            "connection_string",
        }

    def test_a_pem_private_key_header_is_redacted(self) -> None:
        result = validate_output(_pem_block())
        assert PRIVATE_KEY_PLACEHOLDER in result.text
        assert "pem_private_key" in result.redacted

    def test_redact_secrets_returns_text_and_kinds(self) -> None:
        text, kinds = redact_secrets("token=" + _openai_key())
        assert _openai_key() not in text
        assert kinds == ["openai_key"]


class TestStructuredValidation:
    def test_a_valid_response_parses(self) -> None:
        result = validate_output(
            '{"answer": "Because attention weights are learned.", "citations": ["S1"]}',
            schema=TutorAnswer,
        )
        assert result.valid is True
        assert isinstance(result.data, TutorAnswer)
        assert result.data.citations == ["S1"]
        assert result.degraded == []

    def test_prose_instead_of_schema_is_rejected_not_coerced(self) -> None:
        result = validate_output("Attention is all you need.", schema=TutorAnswer)
        assert result.valid is False
        assert result.data is None
        assert "output_invalid" in result.degraded

    def test_an_unknown_field_is_rejected_by_the_strict_model(self) -> None:
        result = validate_output(
            '{"answer": "x", "citations": [], "extra": "not allowed"}', schema=TutorAnswer
        )
        assert result.valid is False
        assert "output_invalid" in result.degraded

    def test_a_credential_in_structured_output_is_still_redacted(self) -> None:
        result = validate_output(
            '{"answer": "the key is ' + _openai_key() + '", "citations": []}',
            schema=TutorAnswer,
        )
        assert _openai_key() not in result.text
        assert "openai_key" in result.redacted


class TestSystemPromptLeak:
    def test_an_echoed_system_prompt_is_redacted(self) -> None:
        leaked = (
            "Sure! Here is my configuration. You are a careful tutor for the course "
            '"Biology". You answer a student\'s question strictly from the evidence.'
        )
        result = validate_output(leaked)
        assert "You are a careful tutor" not in result.text
        assert SYSTEM_PROMPT_PLACEHOLDER in result.text
        assert result.leaked_system_prompt is True
        assert "system_prompt_leak" in result.degraded

    def test_the_untrusted_evidence_preamble_is_detected(self) -> None:
        text = "The region delimited by the tags <untrusted_evidence> contains material."
        redacted, leaked = redact_system_prompt(text)
        assert leaked is True
        assert "The region delimited" not in redacted

    def test_ordinary_prose_is_not_treated_as_a_leak(self) -> None:
        text = "The tutor explains the course material."
        redacted, leaked = redact_system_prompt(text)
        assert leaked is False
        assert redacted == text
