"""Request and response schemas for authentication."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from coursellm.db.models.identity import UserRole


class RegisterRequest(BaseModel):
    """Self-service registration, which creates a tenant and its owner.

    Registration creates a new tenant rather than joining one. Joining is an
    invitation flow, which is a different operation with different
    authorisation; conflating them is how "anyone can add themselves to any
    workspace" bugs happen.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str | None = Field(default=None, max_length=200)
    tenant_name: str | None = Field(
        default=None,
        max_length=200,
        description="Organisation name. Defaults to a name derived from the email domain.",
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=10)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    """Issued credentials.

    ``refresh_token`` is returned in the body rather than set as a cookie because
    the client is a separate origin in the deployed topology and the API does not
    rely on cookies for authentication. The trade-off is that the client must
    store it deliberately; the frontend keeps it out of the render path and
    clears it on logout.
    """

    access_token: str
    refresh_token: str
    # "bearer" is the OAuth token *type* label, not a credential.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access-token lifetime in seconds.")


class UserResponse(BaseModel):
    """A user as returned by the API.

    Note the absence of ``hashed_password``. The previous implementation's user
    schema inherited from its database schema, so the bcrypt hash was serialised
    into ``/auth/me`` and the registration response. Response models here are
    written independently of the ORM models so that adding a sensitive column to
    a table cannot silently publish it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    tenant_id: uuid.UUID
    is_active: bool
    created_at: datetime


class RegistrationResponse(BaseModel):
    user: UserResponse
    tenant_id: uuid.UUID
    tenant_slug: str
    tokens: TokenResponse
