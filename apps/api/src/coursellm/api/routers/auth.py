"""Authentication endpoints: register, login, refresh, logout, me.

Token DTOs are converted with :func:`dataclasses.asdict`, not ``__dict__``.
``IssuedTokens`` is declared with ``slots=True``, so it has no instance
dictionary and ``__dict__`` raises ``AttributeError``.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, status

from coursellm.api.deps import (
    ContextDep,
    SettingsDep,
    TenantSessionDep,
    UnscopedSessionDep,
    UserRepositoryDep,
)
from coursellm.api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegistrationResponse,
    TokenResponse,
    UserResponse,
)
from coursellm.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace and its owner",
    description=(
        "Creates a tenant and its first user, then returns tokens. Registration "
        "always creates a new workspace; joining an existing one is an invitation "
        "flow with different authorisation."
    ),
)
async def register(
    payload: RegisterRequest,
    session: UnscopedSessionDep,
    settings: SettingsDep,
) -> RegistrationResponse:
    user, tenant, tokens = await auth_service.register_user(
        session,
        settings,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        tenant_name=payload.tenant_name,
    )
    return RegistrationResponse(
        user=UserResponse.model_validate(user),
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        tokens=TokenResponse(**asdict(tokens)),
    )


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Exchange credentials for tokens",
    description=(
        "Returns an access token and a refresh token. Every failure returns the "
        "same error so the endpoint cannot be used to enumerate accounts."
    ),
)
async def login(
    payload: LoginRequest,
    session: UnscopedSessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    _, tokens = await auth_service.authenticate(
        session, settings, email=payload.email, password=payload.password
    )
    return TokenResponse(**asdict(tokens))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token",
    description=(
        "Issues a new token pair and revokes the presented refresh token. "
        "Presenting an already-revoked token is treated as a possible theft and "
        "refused."
    ),
)
async def refresh(
    payload: RefreshRequest,
    session: UnscopedSessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    _, tokens = await auth_service.refresh_tokens(
        session, settings, refresh_token=payload.refresh_token
    )
    return TokenResponse(**asdict(tokens))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token",
    description="Idempotent: revoking an unknown or already-revoked token also succeeds.",
)
async def logout(
    payload: LogoutRequest,
    session: UnscopedSessionDep,
    settings: SettingsDep,
) -> None:
    await auth_service.logout(session, settings, refresh_token=payload.refresh_token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="The authenticated user",
    description=(
        "Returns the caller's profile. The response model is defined independently "
        "of the database model, so a sensitive column added to the table cannot "
        "silently appear here."
    ),
)
async def me(context: ContextDep, users: UserRepositoryDep) -> UserResponse:
    user = await users.get_or_raise(context.user_id)
    return UserResponse.model_validate(user)


@router.get(
    "/me/tenant-scoped-session",
    include_in_schema=False,
)
async def _tenant_probe(context: ContextDep, session: TenantSessionDep) -> dict[str, str]:
    """Development aid: prove the request session carries a tenant context.

    Not part of the public surface. It exists so that an operator can confirm,
    against a running instance, that the tenancy variable is bound for request
    sessions rather than only asserted by tests.
    """
    from sqlalchemy import text

    value = (
        await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
    ).scalar_one()
    return {"tenant_id": str(value), "authenticated_tenant": str(context.tenant_id)}
