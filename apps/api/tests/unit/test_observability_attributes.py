"""Unit tests for the span attribute schema and the redaction boundary.

The schema is only a control if it is *closed*: a call site that invents an
attribute name carrying document text must fail a test, not merely be
discouraged in review. These tests pin the allowed set, the forbidden
substrings, the payload redaction filter, the disabled-tracing no-op and the
LangSmith prompt gate.
"""

from __future__ import annotations

import json

import pytest

from coursellm.core.config import Settings
from coursellm.observability import langsmith, tracing
from coursellm.observability.attributes import (
    ALLOWED_ATTRIBUTE_NAMES,
    ATTRIBUTE_NAMES,
    METADATA_NAME_EXCEPTIONS,
    forbidden_substrings,
    is_forbidden_payload_key,
    sanitize_attributes,
)
from coursellm.observability.langsmith import LangSmithHandle

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


class TestAttributeSchema:
    def test_every_attribute_constant_is_in_the_documented_set(self) -> None:
        undeclared = set(ATTRIBUTE_NAMES) - ALLOWED_ATTRIBUTE_NAMES
        assert not undeclared, (
            f"Attribute constants missing from the documented schema: {sorted(undeclared)}"
        )

    def test_the_documented_set_has_no_duplicates(self) -> None:
        assert len(ATTRIBUTE_NAMES) == len(set(ATTRIBUTE_NAMES))

    def test_forbidden_substrings_appear_only_in_documented_metadata_names(self) -> None:
        """No attribute may match prompt/message/content/question/answer/email/name.

        The five exceptions are the document's own identifier and version
        labels (``graph.name``, ``coursellm.llm.prompt_version`` and the three
        ``*.name`` operation/collection labels). Any new match fails this test,
        which is the point: the next ``prompt_text`` constant cannot be added
        quietly.
        """
        offending = {name for name in ATTRIBUTE_NAMES if forbidden_substrings(name)}
        assert offending == set(METADATA_NAME_EXCEPTIONS), (
            f"Attribute names match forbidden payload substrings: "
            f"{sorted(offending - set(METADATA_NAME_EXCEPTIONS))}. Attribute constants "
            f"must be metadata, never payload."
        )

    def test_exceptions_are_the_only_documented_name_or_prompt_lookalikes(self) -> None:
        assert {
            "graph.name",
            "db.collection.name",
            "db.operation.name",
            "gen_ai.operation.name",
            "coursellm.llm.prompt_version",
        } == METADATA_NAME_EXCEPTIONS


class TestPayloadRedaction:
    def test_payload_keys_are_refused(self) -> None:
        for key in (
            "prompt_text",
            "prompt",
            "messages",
            "document_text",
            "document_content",
            "chunk_text",
            "question",
            "answer",
            "email",
            "full_name",
            "api_key",
        ):
            assert is_forbidden_payload_key(key), f"{key!r} must be refused"

    def test_documented_metadata_names_survive(self) -> None:
        for key in ALLOWED_ATTRIBUTE_NAMES:
            assert not is_forbidden_payload_key(key), f"{key!r} is a documented attribute"

    def test_sanitize_drops_payload_and_keeps_metadata(self) -> None:
        dirty = {
            "graph.name": "tutor_graph",
            "coursellm.llm.prompt_version": "tutor.answer@3",
            "prompt_text": "the student's question",
            "document_content": "a passage from a private PDF",
            "email": "student@example.com",
            "question": "What is attention?",
        }
        clean = sanitize_attributes(dirty)
        assert clean == {
            "graph.name": "tutor_graph",
            "coursellm.llm.prompt_version": "tutor.answer@3",
        }


class TestTracingDisabled:
    def test_disabled_tracing_constructs_no_exporter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tracing.reset_tracing()

        def _explode(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("an exporter must not be constructed when tracing is disabled")

        monkeypatch.setattr(tracing, "_build_exporter", _explode)
        handle = tracing.configure_tracing(_settings(otel_enabled=False))
        assert handle.enabled is False
        assert handle.provider is None
        assert handle.exporter is None

    def test_exporter_none_is_disabled_even_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tracing.reset_tracing()
        monkeypatch.setattr(
            tracing,
            "_build_exporter",
            lambda *a, **k: pytest.fail("no exporter for the 'none' exporter"),
        )
        handle = tracing.configure_tracing(
            _settings(otel_enabled=True, otel_traces_exporter="none")
        )
        assert handle.enabled is False

    def test_span_is_a_noop_and_records_nothing(self) -> None:
        tracing.reset_tracing()
        settings = _settings(otel_enabled=False)
        tracing.configure_tracing(settings)
        with tracing.span("retrieval.semantic", **{"prompt_text": "leak"}) as record:
            record.set_attribute("document_content", "leak")
            record.set_error()
            record.record_exception(RuntimeError("x"))
        assert tracing.current_trace_id() is None
        assert tracing.active_handle() is None

    def test_span_yields_a_recorder_protocol(self) -> None:
        tracing.reset_tracing()
        with tracing.span("noop") as record:
            assert isinstance(record, tracing.SpanRecorder)


class TestLangSmithGate:
    def test_disabled_when_switch_is_off(self) -> None:
        handle = langsmith.configure_langsmith(_settings(langsmith_enabled=False))
        assert handle.enabled is False

    def test_disabled_when_key_is_missing(self) -> None:
        handle = langsmith.configure_langsmith(
            _settings(langsmith_enabled=True, langsmith_api_key="")
        )
        assert handle.enabled is False

    def test_runtime_credential_shape_is_not_required_for_the_gate(self) -> None:
        # The gate is the switch plus a non-empty key; the value never has to be
        # realistic for the disabled path, and this test builds it from fragments
        # so no credential-shaped literal exists in the tree.
        handle = langsmith.configure_langsmith(
            _settings(langsmith_enabled=True, langsmith_api_key="ls" + "v2_" + "unit-fixture")
        )
        assert handle.enabled is True

    def test_no_prompt_content_is_passed_when_capture_is_off(self) -> None:
        secret = "a private passage from the student's own PDF"
        handle = LangSmithHandle(enabled=True, project="coursellm-test", capture_prompts=False)
        run_inputs = langsmith.payload(
            handle,
            metadata={"graph": "tutor_graph", "node": "tutor"},
            prompts={"messages": [{"role": "user", "content": secret}]},
        )
        assert "prompts" not in run_inputs
        assert secret not in json.dumps(run_inputs)

    def test_prompt_content_is_passed_only_when_capture_is_on(self) -> None:
        secret = "a private passage from the student's own PDF"
        handle = LangSmithHandle(enabled=True, project="coursellm-test", capture_prompts=True)
        run_inputs = langsmith.payload(handle, prompts={"messages": [{"content": secret}]})
        assert secret in json.dumps(run_inputs)

    def test_disabled_run_is_a_noop_and_imports_nothing(self) -> None:
        secret = "a private passage from the student's own PDF"
        handle = langsmith.configure_langsmith(_settings(langsmith_enabled=False))
        with langsmith.trace_run(
            handle, name="tutor_graph:tutor:p3:v7", prompts={"content": secret}
        ) as run:
            assert run is None
