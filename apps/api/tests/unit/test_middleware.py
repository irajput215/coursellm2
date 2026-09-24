"""Middleware tests: request correlation, security headers, body limits."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


class TestRequestId:
    async def test_generates_a_request_id_when_absent(self, client: AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.headers.get("x-request-id")

    async def test_echoes_a_well_formed_client_request_id(self, client: AsyncClient) -> None:
        response = await client.get("/healthz", headers={"x-request-id": "abc-123-def-456"})
        assert response.headers["x-request-id"] == "abc-123-def-456"

    @pytest.mark.parametrize(
        "hostile",
        [
            "short",
            "x" * 200,
            "has spaces in it",
            "semi;colon",
            "new\nline",
            "../../etc/passwd",
            "unicode-ü-é",
            "<script>alert(1)</script>",
            "a" * 7,  # one below the minimum length
        ],
    )
    async def test_rejects_malformed_client_ids(self, client: AsyncClient, hostile: str) -> None:
        """A client id is a correlation hint, never trusted as identity.

        It is charset- and length-limited so it cannot forge log lines or
        inflate log volume.
        """
        from coursellm.middleware import _client_request_id

        scope = {"type": "http", "headers": [(b"x-request-id", hostile.encode("utf-8", "ignore"))]}
        assert _client_request_id(scope) is None

    @pytest.mark.parametrize(
        "hostile",
        ["short", "x" * 200, "has spaces in it", "semi;colon", "../../etc/passwd"],
    )
    async def test_server_substitutes_its_own_id_for_a_bad_one(
        self, client: AsyncClient, hostile: str
    ) -> None:
        """Even when a bad id reaches the transport, it is replaced, not echoed.

        Non-ASCII values are excluded here because HTTP headers are ASCII by
        specification and the transport rejects them before the application
        sees them; the pure-function test above covers that case directly.
        """
        response = await client.get("/healthz", headers={"x-request-id": hostile})
        assert response.headers["x-request-id"] != hostile
        assert len(response.headers["x-request-id"]) >= 8

    async def test_ids_are_unique_per_request(self, client: AsyncClient) -> None:
        first = (await client.get("/healthz")).headers["x-request-id"]
        second = (await client.get("/healthz")).headers["x-request-id"]
        assert first != second


class TestSecurityHeaders:
    async def test_standard_headers_are_present(self, client: AsyncClient) -> None:
        headers = (await client.get("/healthz")).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "permissions-policy" in headers

    async def test_headers_are_present_on_error_responses(self, client: AsyncClient) -> None:
        """A 404 must be as hardened as a 200; attackers probe the error paths."""
        response = await client.get("/does-not-exist")
        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers.get("x-request-id")


class TestBodySizeLimit:
    async def test_oversized_body_is_rejected_with_413(self, client: AsyncClient) -> None:
        """The limit is enforced from Content-Length before the body is buffered."""
        response = await client.post(
            "/healthz",
            content=b"x" * 1024,
            headers={"content-length": str(50 * 1024 * 1024)},
        )
        assert response.status_code == 413
        body = response.json()
        assert body["error"] == "payload_too_large"
        assert body["request_id"]

    async def test_normal_body_passes(self, client: AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.status_code == 200
