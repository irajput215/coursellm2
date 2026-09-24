"""The filter -> predicate contract, including the tenancy property.

The first test is the security-relevant one: a filter set that narrows nothing
must still produce a tenant predicate, and that predicate must come first. The
last test pins the other half of the same guarantee — ``tenant_id`` is not a
field a caller can set at all.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from coursellm.core.config import Settings
from coursellm.db.models.content import Chunk, SourceType
from coursellm.rag.retrieval.filters import build_predicates
from coursellm.rag.retrieval.types import RetrievalFilters

pytestmark = pytest.mark.unit

_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _predicates(filters: RetrievalFilters, settings: Settings):
    return build_predicates(filters, chunk_alias=Chunk, settings=settings, tenant_id=_TENANT_ID)


def test_tenant_predicate_is_present_first_when_nothing_else_is_filtered(
    test_settings: Settings,
) -> None:
    predicates, params = _predicates(RetrievalFilters(), test_settings)

    assert len(predicates) == 1, "an unfiltered query must still be tenant-scoped"
    assert "tenant_id" in str(predicates[0])
    assert params["tenant_id"] == _TENANT_ID


def test_tenant_predicate_survives_disabled_metadata_filtering(test_settings: Settings) -> None:
    disabled = test_settings.model_copy(update={"metadata_filtering_enabled": False})
    filters = RetrievalFilters(
        course_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        topic="attention",
        page=3,
        source_types=[SourceType.LECTURE],
    )

    predicates, params = _predicates(filters, disabled)

    assert len(predicates) == 1
    assert "tenant_id" in str(predicates[0])
    assert set(params) == {"tenant_id"}


def test_metadata_filters_each_add_a_bound_predicate(test_settings: Settings) -> None:
    course_id = uuid.uuid4()
    document_id = uuid.uuid4()
    filters = RetrievalFilters(
        course_id=course_id,
        document_id=document_id,
        topic="attention",
        page=7,
    )

    predicates, params = _predicates(filters, test_settings)

    assert len(predicates) == 5  # tenant + course + document + topic + page
    assert params["filter_course_id"] == course_id
    assert params["filter_document_id"] == document_id
    assert params["filter_topic"] == "attention"
    assert params["filter_page"] == 7
    joined = " ".join(str(predicate) for predicate in predicates)
    assert "filter_course_id" in joined
    assert "filter_document_id" in joined
    assert "filter_topic" in joined
    assert "filter_page" in joined


def test_source_types_builds_an_exists_clause_on_documents(test_settings: Settings) -> None:
    filters = RetrievalFilters(source_types=[SourceType.LECTURE, SourceType.PAPER])

    predicates, _ = _predicates(filters, test_settings)

    assert len(predicates) == 2
    exists_clause = str(predicates[1]).upper()
    assert "EXISTS" in exists_clause
    assert "DOCUMENTS" in exists_clause
    # An EXISTS subquery keeps the outer statement one row per chunk; a join
    # would not.
    assert "JOIN" not in str(predicates[1]).upper()


def test_source_types_is_dropped_when_metadata_filtering_is_disabled(
    test_settings: Settings,
) -> None:
    disabled = test_settings.model_copy(update={"metadata_filtering_enabled": False})

    predicates, _ = _predicates(RetrievalFilters(source_types=[SourceType.LECTURE]), disabled)

    assert len(predicates) == 1
    assert "EXISTS" not in str(predicates[0]).upper()


def test_unknown_filter_keys_are_rejected(test_settings: Settings) -> None:
    with pytest.raises(PydanticValidationError):
        RetrievalFilters(unknown_dimension="value")  # type: ignore[call-arg]


def test_retrieval_filters_has_no_tenant_field() -> None:
    """Tenancy comes from the scope, so there must be nothing to supply."""
    assert "tenant_id" not in RetrievalFilters.model_fields
    with pytest.raises(PydanticValidationError):
        RetrievalFilters(tenant_id=_TENANT_ID)  # type: ignore[call-arg]


def test_only_tenant_scoped_filter_dimensions_are_accepted() -> None:
    assert set(RetrievalFilters.model_fields) == {
        "course_id",
        "document_id",
        "topic",
        "page",
        "source_types",
    }
