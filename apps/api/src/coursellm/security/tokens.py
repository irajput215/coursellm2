# ruff: noqa: S105  -- TOKEN_ISSUER/TOKEN_AUDIENCE are public identifiers, not credentials.
"""JWT issuing and verification.

Every claim here exists to close a specific attack or verification gap that a
bare ``{"sub", "exp"}`` token leaves open:

| Claim | Purpose |
|-------|---------|
| ``sub`` | The user id. |
| ``tid`` | The tenant id. This is what makes tenancy derived rather than supplied. |
| ``role`` | Coarse authorisation, checked without a database round trip. |
| ``typ`` | ``access`` or ``refresh``. Stops a refresh token being
replayed as an access token — otherwise a trivial escalation, since both are
signed by the same key. |
| ``jti`` | Refresh-token identity, enabling revocation. |
| ``iat`` / ``nbf`` | Issue and validity-start times. |
| ``iss`` / ``aud`` | Issuer and audience, verified on decode. Without
these, a token minted for a different service sharing the signing key would be
accepted here. |

The algorithm is pinned by configuration and asserted at decode time, so the
classic ``alg: none`` and RS256-to-HS256 confusion attacks are rejected before
signature verification is attempted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from coursellm.core.config import Settings
from coursellm.core.errors import AuthenticationError
from coursellm.db.models.identity import UserRole

TokenType = Literal["access", "refresh"]

# Fixed for this service. A token minted for another audience is not valid here
# even if it was signed with the same key.
# Neither value is a secret; both are public identifiers.
TOKEN_ISSUER = "coursellm"
TOKEN_AUDIENCE = "coursellm-api"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified claims, typed."""

    subject: uuid.UUID
    tenant_id: uuid.UUID
    role: UserRole
    token_type: TokenType
    jti: uuid.UUID | None
    expires_at: datetime


def _encode(settings: Settings, payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode(settings: Settings, token: str, *, expected_type: TokenType) -> TokenClaims:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            # Pinning the algorithm list is what defeats algorithm-confusion
            # attacks. ``jwt.decode`` would otherwise honour the token's header.
            algorithms=[settings.jwt_algorithm],
            issuer=TOKEN_ISSUER,
            audience=TOKEN_AUDIENCE,
            options={
                "require": ["exp", "iat", "sub", "tid", "typ"],
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        # Deliberately one message for every malformed-token case. Distinguishing
        # "bad signature" from "wrong audience" tells an attacker which part to
        # iterate on.
        raise AuthenticationError("The provided token is not valid.") from exc

    if payload.get("typ") != expected_type:
        raise AuthenticationError("The provided token is not valid for this operation.")

    try:
        return TokenClaims(
            subject=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            role=UserRole(payload["role"]),
            token_type=expected_type,
            jti=uuid.UUID(payload["jti"]) if payload.get("jti") else None,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("The provided token is not valid.") from exc


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: UserRole,
) -> tuple[str, datetime]:
    """Issue a short-lived access token. Returns the token and its expiry."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    token = _encode(
        settings,
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "role": role.value,
            "typ": "access",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        },
    )
    return token, expires_at


def create_refresh_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: UserRole,
) -> tuple[str, datetime, uuid.UUID]:
    """Issue a refresh token. Returns the token, its expiry and its ``jti``.

    The ``jti`` is returned so the caller can record it if the token is later
    revoked; revocation is a denylist entry keyed by this value.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    jti = uuid.uuid4()
    token = _encode(
        settings,
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "role": role.value,
            "typ": "refresh",
            "jti": str(jti),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        },
    )
    return token, expires_at, jti


def decode_access_token(settings: Settings, token: str) -> TokenClaims:
    return _decode(settings, token, expected_type="access")


def decode_refresh_token(settings: Settings, token: str) -> TokenClaims:
    return _decode(settings, token, expected_type="refresh")


def access_token_ttl_seconds(settings: Settings) -> int:
    return settings.access_token_expire_minutes * 60
