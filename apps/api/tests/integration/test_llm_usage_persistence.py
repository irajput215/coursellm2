"""End-to-end persistence of model usage, against real PostgreSQL.

These are the only tests that prove the accounting actually reaches the database
under Row-Level Security: the gateway writes through the restricted
``coursellm_app`` role, so a missing policy, a missing tenancy variable or a
wrong tenant would make the write fail or the row invisible.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from coursellm.core.errors import UpstreamError
from coursellm.db.session import get_session_factory
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.llm.cost import estimate_cost
from coursellm.llm.gateway import LiteLLMGateway
from coursellm.llm.types import LLMRequest, LLMScope, ModelTask, bind_llm_scope

pytestmark = pytest.mark.integration

PRIMARY_MODEL = "openai/gpt-4o-mini"

_SELECT_USAGE = (
    "SELECT tenant_id, user_id, request_id, model, provider, prompt_tokens, "
    "completion_tokens, total_tokens, cost_usd, priced, error_type, attempt "
    "FROM llm_usage"
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _settings(pg_settings: Settings, **overrides: object) -> Settings:
    """Enable the real gateway on top of the RLS-enforcing test settings."""
    values: dict[str, object] = {
        "llm_enabled": True,
        "llm_routing_enabled": True,
        "llm_max_retries": 0,
        "fallback_model": "",
        "llm_request_timeout_seconds": 5,
    }
    values.update(overrides)
    return pg_settings.model_copy(update=values)


def _request() -> LLMRequest:
    return LLMRequest(
        task=ModelTask.TUTORING,
        messages=[{"role": "user", "content": "Explain eigenvalues."}],
        purpose="tutor.answer",
    )


def _success_response(
    *, content: str = "A derivative measures change.", prompt: int = 12, completion: int = 7
) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
    )


async def test_completing_a_request_writes_exactly_one_usage_row(
    pg_settings: Settings,
    seeded: Any,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(pg_settings)
    factory = await get_session_factory(settings)

    async def fake_acompletion(**_kwargs: Any) -> Any:
        return _success_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    gateway = LiteLLMGateway(settings, factory, sleep=_no_sleep)
    request_id = uuid.uuid4()
    scope = LLMScope(
        tenant_id=seeded.tenant_a_id,
        user_id=seeded.user_a_id,
        request_id=request_id,
    )

    with bind_llm_scope(scope):
        response = await gateway.complete(_request())

    assert response.text == "A derivative measures change."
    assert response.model == PRIMARY_MODEL

    async with owner_engine.connect() as connection:
        rows = (await connection.execute(text(_SELECT_USAGE))).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["tenant_id"] == seeded.tenant_a_id
    assert row["user_id"] == seeded.user_a_id
    assert row["request_id"] == request_id
    assert row["model"] == PRIMARY_MODEL
    assert row["provider"] == "openai"
    assert row["prompt_tokens"] == 12
    assert row["completion_tokens"] == 7
    assert row["total_tokens"] == 19
    assert row["priced"] is True
    assert float(row["cost_usd"]) == pytest.approx(estimate_cost(PRIMARY_MODEL, 12, 7), abs=1e-6)
    assert row["error_type"] is None


async def test_tenant_b_cannot_see_tenant_a_usage_rows(
    pg_settings: Settings, seeded: Any, owner_engine: AsyncEngine
) -> None:
    """Row-Level Security on the new table, proved with the restricted role.

    The row is written as the owner (which bypasses RLS, as a fixture must), and
    then read back through the application role under two different tenants.
    """
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO llm_usage "
                "(id, tenant_id, task, purpose, model, provider, used_fallback, "
                " prompt_tokens, completion_tokens, total_tokens, cost_usd, priced, "
                " latency_ms, attempt) "
                "VALUES (:id, :tenant_id, 'tutoring', 'tutor.answer', :model, 'openai', "
                " false, 10, 5, 15, 0.25, true, 42, 1)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(seeded.tenant_a_id),
                "model": PRIMARY_MODEL,
            },
        )

    async with tenant_session(pg_settings, TenantScope(seeded.tenant_b_id)) as session:
        visible_to_b = (await session.execute(text("SELECT count(*) FROM llm_usage"))).scalar_one()

    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        visible_to_a = (await session.execute(text("SELECT count(*) FROM llm_usage"))).scalar_one()

    assert visible_to_b == 0
    assert visible_to_a == 1


async def test_failed_attempt_writes_a_row_with_an_error_type(
    pg_settings: Settings,
    seeded: Any,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(pg_settings)
    factory = await get_session_factory(settings)

    async def failing_acompletion(**_kwargs: Any) -> Any:
        raise litellm.RateLimitError(
            message="slow down", llm_provider="openai", model="gpt-4o-mini"
        )

    monkeypatch.setattr(litellm, "acompletion", failing_acompletion)
    gateway = LiteLLMGateway(settings, factory, sleep=_no_sleep)
    scope = LLMScope(tenant_id=seeded.tenant_a_id, user_id=seeded.user_a_id)

    with pytest.raises(UpstreamError), bind_llm_scope(scope):
        await gateway.complete(_request())

    async with owner_engine.connect() as connection:
        rows = (await connection.execute(text(_SELECT_USAGE))).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["tenant_id"] == seeded.tenant_a_id
    assert row["error_type"] == "RateLimitError"
    assert row["prompt_tokens"] == 0
    assert row["completion_tokens"] == 0
    assert row["attempt"] == 1
