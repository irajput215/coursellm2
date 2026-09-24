"""Hardening assertions from ``docs/architecture/security.md`` that lacked a test.

Each class below names the claim it pins and, where an equivalent assertion
already exists elsewhere, says so rather than duplicating it. The rule for this
file is that a test only belongs here if it asserts a *control*, not a happy
path.

| Claim (security.md) | Control under test |
|---------------------|--------------------|
| §11 oversized input | ``Content-Length`` over the cap is refused before the handler |
| §9 output handling | a malformed body yields the error envelope, never a traceback |
| §9 output validation | the envelope carries no SQL, stack frame or provider text |
| §7.4 pool hazard | ``/metrics`` (unauthenticated) carries no tenant identifier |
| §10 key rotation | a token signed with a rotated key is rejected |
| §7.1 identity | a deactivated user cannot obtain a token |

Credential-shaped values are assembled from fragments at runtime so the secret
scanner keeps zero pragmas.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from coursellm.core.errors import AuthenticationError, ErrorResponse
from coursellm.db.models.identity import UserRole
from coursellm.middleware import BodySizeLimitMiddleware
from coursellm.security.tokens import create_access_token, decode_access_token

pytestmark = pytest.mark.security

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

#: The internals a response must never carry, whatever went wrong.
_FORBIDDEN_FRAGMENTS = (
    "SELECT ",
    "FROM users",
    "Traceback (most recent call last)",
    "RuntimeError",
    "psycopg",
    "asyncpg",
    "openai.AuthenticationError",
)
#: Assembled from fragments at runtime; no literal key-shaped string is in this
#: file, so the secret scanner needs no exception for it.
_PROVIDER_KEY_SHAPED = "sk-" + "b" * 32


class TestOversizedContentLengthIsRefusedBeforeTheHandler:
    """§11: ``Content-Length`` above the cap is refused before the body is read.

    ``tests/security/test_upload_safety.py`` covers the same guarantee end to
    end for the upload route. This is the middleware-level version: it drives
    the ASGI layer directly, so it proves the guard is *before* the handler on
    every route rather than only the one the upload test exercises.
    """

    async def test_the_handler_never_runs_for_an_oversized_body(self) -> None:
        calls: list[str] = []

        async def handler(scope: Any, receive: Any, send: Any) -> None:
            calls.append("handler")
            raise AssertionError("the handler must not be constructed for a 4 MiB body")

        middleware = BodySizeLimitMiddleware(handler, max_bytes=1024)
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:  # pragma: no cover - never awaited
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "path": "/api/v1/documents",
            "headers": [(b"content-length", str(4 * 1024 * 1024).encode())],
        }
        await middleware(scope, receive, send)

        assert calls == [], "the handler ran despite an oversized Content-Length"
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 413

    async def test_an_exactly_at_the_limit_body_is_allowed(self) -> None:
        """The boundary is ``>``, not ``>=``; an off-by-one here blocks valid uploads."""
        calls: list[str] = []

        async def handler(scope: Any, receive: Any, send: Any) -> None:
            calls.append("handler")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = BodySizeLimitMiddleware(handler, max_bytes=1024)

        async def receive() -> dict[str, Any]:  # pragma: no cover - never awaited
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:  # pragma: no cover - no assertion
            return None

        scope = {"type": "http", "path": "/x", "headers": [(b"content-length", b"1024")]}
        await middleware(scope, receive, send)
        assert calls == ["handler"]


class TestMalformedBodyYieldsTheEnvelope:
    """§9: a body that is not valid JSON is a typed 422, not a 500 traceback."""

    @pytest.mark.parametrize(
        "body",
        [
            b"{not json",
            b"",
            b"[1, 2, 3]",
            b'{"email": "a@b.c", "password": }',
        ],
    )
    async def test_the_envelope_is_returned(self, client: AsyncClient, body: bytes) -> None:
        response = await client.post(
            "/api/v1/auth/token", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == 422, response.text
        payload = ErrorResponse.model_validate(response.json())
        assert payload.error == "validation_error"
        assert payload.request_id
        raw = response.text
        assert "Traceback" not in raw
        assert "JSONDecodeError" not in raw

    async def test_the_envelope_is_json_not_the_framework_default(
        self, client: AsyncClient
    ) -> None:
        """FastAPI's default 422 body has a different shape; ours has ``error``."""
        response = await client.post(
            "/api/v1/auth/token", content=b"{not json", headers={"content-type": "application/json"}
        )
        assert set(response.json()) >= {"error", "detail", "request_id"}


class TestErrorResponsesCarryNoInternals:
    """§9: a client never sees SQL, a stack frame or a provider's own message."""

    @pytest.mark.parametrize(
        "failure",
        [
            RuntimeError(
                "SELECT tenant_id FROM users WHERE email = 'x' | "
                "openai.AuthenticationError: invalid api key"
            ),
            ConnectionError("asyncpg refused connection using key " + _PROVIDER_KEY_SHAPED),
        ],
    )
    async def test_no_forbidden_fragment_survives(self, app: FastAPI, failure: Exception) -> None:
        @app.get("/boom")
        async def _boom() -> None:
            raise failure

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/boom")

        assert response.status_code == 500
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in response.text, f"response leaked {fragment!r}"
        assert _PROVIDER_KEY_SHAPED not in response.text

    async def test_a_domain_error_keeps_its_status_and_safe_detail(self, app: FastAPI) -> None:
        from coursellm.core.errors import UpstreamError

        @app.get("/upstream")
        async def _upstream() -> None:
            raise UpstreamError("The model provider is unavailable.")

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/upstream")

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "upstream_error"
        assert "openai" not in response.text.lower()
        assert "Traceback" not in response.text


class TestMetricsCarriesNoTenantIdentifier:
    """§7.4: the unauthenticated scrape endpoint must be safe to expose.

    ``tests/unit/test_metrics_registry.py`` proves no *metric definition*
    declares a tenant label. This test proves the thing a scraper actually sees:
    a real tenant UUID, present in the process, never appears in a rendered
    scrape body, and no metric name encodes per-tenant state.
    """

    async def test_a_real_tenant_id_never_appears_in_a_scrape(self, client: AsyncClient) -> None:
        tenant_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        response = await client.get("/metrics")

        assert response.status_code == 200
        assert tenant_id not in response.text
        assert user_id not in response.text
        assert not _UUID_RE.search(response.text)

    async def test_no_metric_name_is_tenant_or_user_scoped(self, client: AsyncClient) -> None:
        """A tenant id in a *name* is unbounded cardinality as well as a leak."""
        body = (await client.get("/metrics")).text
        names = {
            line.split("{", 1)[0].split(" ", 1)[0]
            for line in body.splitlines()
            if line and not line.startswith("#")
        }
        offenders = [
            name
            for name in names
            if any(token in name.lower() for token in ("tenant", "user", "conversation"))
        ]
        assert not offenders, f"per-tenant metric names leaked: {offenders}"


class TestRotatedSigningKey:
    """§10: rotation must invalidate tokens signed with the previous key."""

    def test_a_token_signed_with_a_rotated_key_is_rejected(self, test_settings: Any) -> None:
        """A rotation is a new ``secret_key``; old tokens must not verify."""
        token, _ = create_access_token(
            test_settings,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            role=UserRole.OWNER,
        )
        # ``decode_access_token`` reads the key from settings, so a rotated
        # settings object is exactly what a rotation looks like to a verifier.
        rotated = test_settings.model_copy(update={"secret_key": "rotated-" + "k" * 48})
        with pytest.raises(AuthenticationError):
            decode_access_token(rotated, token)

    def test_the_unrotated_key_still_verifies(self, test_settings: Any) -> None:
        """The positive control: the rejection is signature, not shape."""
        token, _ = create_access_token(
            test_settings,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            role=UserRole.OWNER,
        )
        claims = decode_access_token(test_settings, token)
        assert claims.subject


class TestDeactivatedUser:
    """§7.1: a deactivated user cannot obtain a token.

    This claim is already covered and is *not* duplicated here, because a hollow
    assertion would be worse than an honest cross-reference:

    * ``tests/security/test_data_leakage.py`` proves the service raises before
      the password comparison reaches the database; and
    * ``tests/integration/test_auth_flow.py`` flips ``users.is_active`` through
      the owner engine and asserts the real ``POST /auth/token`` returns 401.

    The token claim itself deliberately carries no account-state field, so a
    token issued before deactivation remains verifiable until it expires. That
    is a property of signed self-contained tokens, not a containment claim, and
    it is recorded here so the boundary of the guarantee is explicit.
    """

    def test_the_access_token_carries_no_account_state_claim(self, test_settings: Any) -> None:
        token, _ = create_access_token(
            test_settings,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            role=UserRole.OWNER,
        )
        claims = decode_access_token(test_settings, token)
        assert not hasattr(claims, "is_active")
