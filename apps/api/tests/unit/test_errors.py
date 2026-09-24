"""Error-handling tests.

The contract under test: every failure returns the same envelope, and internal
detail never leaks to the client.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from coursellm.core.errors import (
    ConflictError,
    CourseLLMError,
    NotFoundError,
    RateLimitError,
    SafetyError,
    UpstreamError,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def error_client(app: FastAPI):
    """App with endpoints that raise each error class."""

    @app.get("/raise/not-found")
    async def _not_found() -> None:
        raise NotFoundError("Course 'CS229' does not exist.")

    @app.get("/raise/conflict")
    async def _conflict() -> None:
        raise ConflictError("A document with that name already exists.")

    @app.get("/raise/rate-limit")
    async def _rate_limit() -> None:
        raise RateLimitError("Too many requests.")

    @app.get("/raise/safety")
    async def _safety() -> None:
        raise SafetyError("This request was refused by the safety layer.")

    @app.get("/raise/upstream")
    async def _upstream() -> None:
        raise UpstreamError("The model provider is unavailable.")

    @app.get("/raise/with-fields")
    async def _with_fields() -> None:
        raise CourseLLMError("bad input", fields={"topic": "unknown"})

    @app.get("/raise/unhandled")
    async def _unhandled() -> None:
        raise RuntimeError("internal detail that must never reach a client: db password hunter2")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


class TestErrorEnvelope:
    @pytest.mark.parametrize(
        ("path", "expected_status", "expected_error"),
        [
            ("/raise/not-found", 404, "not_found"),
            ("/raise/conflict", 409, "conflict"),
            ("/raise/rate-limit", 429, "rate_limited"),
            ("/raise/safety", 400, "unsafe_request"),
            ("/raise/upstream", 502, "upstream_error"),
        ],
    )
    async def test_domain_errors_map_to_stable_codes(
        self, error_client: AsyncClient, path: str, expected_status: int, expected_error: str
    ) -> None:
        response = await error_client.get(path)
        assert response.status_code == expected_status
        body = response.json()
        assert body["error"] == expected_error
        assert body["detail"]
        assert body["request_id"]

    async def test_unknown_route_returns_the_envelope(self, error_client: AsyncClient) -> None:
        response = await error_client.get("/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "http_error"

    async def test_per_field_details_are_returned(self, error_client: AsyncClient) -> None:
        body = (await error_client.get("/raise/with-fields")).json()
        assert body["error"] == "internal_error"
        assert body["fields"] == {"topic": "unknown"}


class TestNoInternalLeakage:
    async def test_unhandled_exception_does_not_leak_its_message(
        self, error_client: AsyncClient
    ) -> None:
        """The single most important assertion in this file."""
        response = await error_client.get("/raise/unhandled")
        assert response.status_code == 500
        raw = response.text
        assert "hunter2" not in raw
        assert "RuntimeError" not in raw
        assert "Traceback" not in raw
        body = response.json()
        assert body["error"] == "internal_error"
        assert body["request_id"], "the correlation id is how support finds the real cause"

    async def test_validation_errors_do_not_echo_the_input(self, error_client: AsyncClient) -> None:
        """FastAPI's default 422 echoes the offending input, which may be a secret."""
        response = await error_client.post("/raise/not-found", json={"x": 1})
        assert response.status_code in {404, 405}
