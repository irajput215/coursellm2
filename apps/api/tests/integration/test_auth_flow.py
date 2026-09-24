"""End-to-end authentication and course-authorisation behaviour over HTTP.

The API boundary is exercised wherever the assertion is about what a client can
observe (status codes, response bodies, token rotation). Where the interesting
guarantee lives below HTTP -- for example that a tenant owner is created with the
right role -- the HTTP response is still the evidence, but the follow-up call is
made through the API rather than the service layer.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

REGISTER = "/api/v1/auth/register"
TOKEN = "/api/v1/auth/token"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
COURSES = "/api/v1/courses"

PASSWORD = "correct-horse-battery-staple"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(
    client: AsyncClient,
    *,
    email: str,
    tenant_name: str,
    password: str = PASSWORD,
) -> dict[str, Any]:
    response = await client.post(
        REGISTER,
        json={
            "email": email,
            "password": password,
            "full_name": "Test Owner",
            "tenant_name": tenant_name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
async def test_registration_returns_tokens_without_leaking_password_material(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        REGISTER,
        json={
            "email": "fresh-owner@example.com",
            "password": PASSWORD,
            "full_name": "Fresh Owner",
            "tenant_name": "Fresh University",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"]
    # The old implementation serialised the ORM model, publishing the bcrypt
    # hash. The response model is independent of the table for exactly this
    # reason, and this is the assertion that keeps it that way.
    assert "hashed_password" not in response.text
    assert "password" not in response.text


async def test_registration_creates_an_owner_in_a_new_tenant(
    api_client: AsyncClient,
) -> None:
    registered = await _register(
        api_client, email="owner-a@example.com", tenant_name="Owner A University"
    )

    me = await api_client.get(ME, headers=_bearer(registered["tokens"]["access_token"]))

    assert me.status_code == 200
    profile = me.json()
    assert profile["role"] == "owner"
    assert profile["tenant_id"] == registered["tenant_id"]


async def test_duplicate_email_registration_conflicts_rather_than_failing(
    api_client: AsyncClient,
) -> None:
    await _register(api_client, email="duplicate@example.com", tenant_name="First Tenant")

    second = await api_client.post(
        REGISTER,
        json={
            "email": "duplicate@example.com",
            "password": PASSWORD,
            "full_name": "Second Attempt",
            "tenant_name": "Second Tenant",
        },
    )

    assert second.status_code == 409
    assert second.status_code != 500


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def test_login_returns_tokens_and_rejects_bad_credentials_indistinguishably(
    api_client: AsyncClient,
) -> None:
    await _register(api_client, email="login@example.com", tenant_name="Login University")

    # The same request id makes the error envelopes byte-identical; a client
    # cannot tell a wrong password from an unknown account.
    correlation = {"x-request-id": "enumeration-probe-0001"}

    success = await api_client.post(
        TOKEN, json={"email": "login@example.com", "password": PASSWORD}, headers=correlation
    )
    wrong_password = await api_client.post(
        TOKEN,
        json={"email": "login@example.com", "password": "definitely-not-the-password"},
        headers=correlation,
    )
    unknown_email = await api_client.post(
        TOKEN,
        json={"email": "nobody@example.com", "password": PASSWORD},
        headers=correlation,
    )

    assert success.status_code == 200
    assert success.json()["access_token"]
    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_me_requires_a_bearer_token(api_client: AsyncClient) -> None:
    missing = await api_client.get(ME)
    garbage = await api_client.get(ME, headers=_bearer("this-is-not-a-jwt"))

    assert missing.status_code == 401
    assert garbage.status_code == 401


async def test_deactivated_user_cannot_log_in(
    api_client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    await _register(api_client, email="deactivated@example.com", tenant_name="Dormant University")

    # Simulate an administrator deactivating the account. The owner engine is
    # used because the app role can only see rows inside its own tenant, and the
    # login lookup deliberately runs outside RLS.
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET is_active = false WHERE email = :email"),
            {"email": "deactivated@example.com"},
        )

    response = await api_client.post(
        TOKEN, json={"email": "deactivated@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Refresh and logout
# ---------------------------------------------------------------------------
async def test_refresh_rotates_the_token_and_refuses_reuse(api_client: AsyncClient) -> None:
    registered = await _register(
        api_client, email="rotator@example.com", tenant_name="Rotation University"
    )
    original = registered["tokens"]["refresh_token"]

    rotated = await api_client.post(REFRESH, json={"refresh_token": original})
    reused = await api_client.post(REFRESH, json={"refresh_token": original})

    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != original
    # Presenting the rotated-away token is treated as a possible theft.
    assert reused.status_code == 401


async def test_logout_revokes_the_refresh_token(api_client: AsyncClient) -> None:
    registered = await _register(
        api_client, email="logout@example.com", tenant_name="Logout University"
    )
    refresh_token = registered["tokens"]["refresh_token"]

    logged_out = await api_client.post(LOGOUT, json={"refresh_token": refresh_token})
    afterwards = await api_client.post(REFRESH, json={"refresh_token": refresh_token})

    assert logged_out.status_code == 204
    assert afterwards.status_code == 401


# ---------------------------------------------------------------------------
# Course authorisation
# ---------------------------------------------------------------------------
async def test_courses_are_listed_only_for_their_own_tenant(api_client: AsyncClient) -> None:
    tenant_a = await _register(api_client, email="courses-a@example.com", tenant_name="Course A")
    tenant_b = await _register(api_client, email="courses-b@example.com", tenant_name="Course B")

    created = await api_client.post(
        COURSES,
        json={"name": "Linear Algebra", "code": "MATH201"},
        headers=_bearer(tenant_a["tokens"]["access_token"]),
    )
    listed_a = await api_client.get(COURSES, headers=_bearer(tenant_a["tokens"]["access_token"]))
    listed_b = await api_client.get(COURSES, headers=_bearer(tenant_b["tokens"]["access_token"]))

    assert created.status_code == 201, created.text
    assert [course["id"] for course in listed_a.json()] == [created.json()["id"]]
    assert listed_b.json() == []


async def test_another_tenants_course_is_not_found_not_forbidden(
    api_client: AsyncClient,
) -> None:
    tenant_a = await _register(api_client, email="hidden-a@example.com", tenant_name="Hidden A")
    tenant_b = await _register(api_client, email="hidden-b@example.com", tenant_name="Hidden B")

    created = await api_client.post(
        COURSES,
        json={"name": "Private Course"},
        headers=_bearer(tenant_a["tokens"]["access_token"]),
    )
    assert created.status_code == 201, created.text

    response = await api_client.get(
        f"{COURSES}/{created.json()['id']}",
        headers=_bearer(tenant_b["tokens"]["access_token"]),
    )

    # 403 would confirm that the course exists somewhere; 404 does not.
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------
async def test_registration_rejects_a_password_below_the_policy(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        REGISTER,
        json={
            "email": "short-password@example.com",
            "password": "short",
            "tenant_name": "Weak Password University",
        },
    )

    assert response.status_code == 422
