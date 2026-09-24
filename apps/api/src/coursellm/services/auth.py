"""Authentication use cases.

Registration, login, refresh and logout. Kept out of the router so that the same
logic is reachable from the CLI, a worker, or a test without HTTP.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.errors import AuthenticationError, ConflictError, ValidationError
from coursellm.core.logging import get_logger
from coursellm.db.models.identity import RefreshTokenRevocation, Tenant, User, UserRole
from coursellm.db.tenancy import TenantScope, set_tenant_guc
from coursellm.repositories.identity import AuthLookup, TenantDirectory, UserRepository
from coursellm.security.passwords import (
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from coursellm.security.tokens import (
    TokenClaims,
    access_token_ttl_seconds,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)

logger = get_logger(__name__)

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


def _slugify(value: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", value.strip().lower()).strip("-")
    return slug[:60] or "workspace"


async def register_user(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    full_name: str | None,
    tenant_name: str | None,
) -> tuple[User, Tenant, IssuedTokens]:
    """Create a tenant and its owner.

    Everything happens in one transaction: a tenant without its owner would be an
    unreachable orphan, and an owner without a tenant could not exist under the
    foreign key. Either both rows land or neither does.
    """
    validate_password_policy(password)
    normalised_email = email.strip().lower()

    directory = TenantDirectory(session)
    display_name = (tenant_name or normalised_email.split("@")[-1]).strip() or "My workspace"
    slug_base = _slugify(display_name)
    # Slugs are derived from names, which are not unique, so a suffix is added
    # once a collision is detected rather than failing registration.
    slug = slug_base
    suffix = 1
    while await directory.slug_exists(slug):
        suffix += 1
        slug = f"{slug_base}-{suffix}"[:60]

    tenant = await directory.create(name=display_name, slug=slug)

    # The users table is protected by RLS. Until the tenancy variable is set,
    # the insert would be rejected by the WITH CHECK clause — correctly, since a
    # row with no ambient tenant is exactly what the policy exists to prevent.
    # Setting it here, inside the same transaction, is how registration
    # legitimately establishes a context that did not exist a moment ago.
    await set_tenant_guc(session, tenant.id)

    users = UserRepository(session, tenant_scope_of(tenant.id))
    hashed = hash_password(password)
    user = await users.create(
        email=normalised_email,
        hashed_password=hashed,
        full_name=full_name,
        role=UserRole.OWNER,
    )

    try:
        await session.flush()
    except IntegrityError as exc:
        # The unique index on email is the authority. Checking first and then
        # inserting is a race; letting the database decide is not.
        await session.rollback()
        raise ConflictError("An account with that email address already exists.") from exc

    tokens = _issue(settings, user)
    logger.info("user_registered", user_id=str(user.id), tenant_id=str(tenant.id))
    return user, tenant, tokens


async def authenticate(
    session: AsyncSession, settings: Settings, *, email: str, password: str
) -> tuple[User, IssuedTokens]:
    """Verify credentials and issue tokens.

    Every failure returns the same error. Distinguishing "no such account" from
    "wrong password" turns the login endpoint into an account-enumeration oracle.
    """
    generic_failure = AuthenticationError("Incorrect email or password.")

    lookup = await AuthLookup(session).find_for_login(email)
    if lookup is None:
        # Perform a dummy verification so that a missing account and a wrong
        # password take a comparable amount of time, rather than the missing
        # account returning measurably faster.
        _dummy_verify(password)
        raise generic_failure

    if not verify_password(password, lookup.hashed_password):
        raise generic_failure

    # Checked only after the password is known to be correct, so that account
    # state cannot be probed without credentials.
    if not lookup.is_active:
        raise AuthenticationError("This account has been deactivated.")
    if not lookup.tenant_is_active:
        raise AuthenticationError("This workspace has been deactivated.")

    # The tenant is now known, so RLS can be applied for the remaining reads.
    await set_tenant_guc(session, lookup.tenant_id)
    users = UserRepository(session, tenant_scope_of(lookup.tenant_id))
    user = await users.get(lookup.user_id)
    if user is None:  # pragma: no cover - the lookup and the scoped read disagree
        raise generic_failure

    if needs_rehash(user.hashed_password):
        # Transparent upgrade when the cost factor is raised, so that a policy
        # change does not require a forced password reset for every account.
        user.hashed_password = hash_password(password)

    user.last_login_at = _utcnow()
    tokens = _issue(settings, user)
    logger.info("user_authenticated", user_id=str(user.id), tenant_id=str(user.tenant_id))
    return user, tokens


async def refresh_tokens(
    session: AsyncSession, settings: Settings, *, refresh_token: str
) -> tuple[User, IssuedTokens]:
    """Exchange a valid refresh token for a new pair, rotating the old one.

    Rotation with revocation means a stolen refresh token is usable at most once
    before the legitimate client's next refresh invalidates it, which makes theft
    detectable rather than silent.
    """
    claims = decode_refresh_token(settings, refresh_token)

    await set_tenant_guc(session, claims.tenant_id)
    users = UserRepository(session, tenant_scope_of(claims.tenant_id))

    if claims.jti is None:
        raise AuthenticationError("The provided token is not valid.")

    # The revocation table is tenant-scoped, so this read is itself subject to
    # RLS: a token whose tenant claim was forged cannot consult another tenant's
    # denylist, and the scoped read below would find no matching row.
    existing = await session.execute(
        select(RefreshTokenRevocation).where(RefreshTokenRevocation.jti == claims.jti)
    )
    if existing.scalar_one_or_none() is not None:
        # Presenting an already-rotated token is a strong signal that it was
        # captured. The token is revoked regardless; refusing is the safe answer.
        logger.warning("refresh_token_reuse_detected", jti=str(claims.jti))
        raise AuthenticationError("This session has been revoked. Please sign in again.")

    user = await users.get(claims.subject)
    if user is None or not user.is_active:
        raise AuthenticationError("This account is no longer active.")

    # Revoke the presented token before issuing a replacement.
    await _revoke(session, claims, reason="rotation")
    tokens = _issue(settings, user)
    return user, tokens


async def logout(session: AsyncSession, settings: Settings, *, refresh_token: str) -> None:
    """Revoke a refresh token.

    Best-effort by design: an invalid or already-revoked token produces the same
    outcome as a successful logout from the caller's perspective, so the endpoint
    cannot be used to probe token validity.
    """
    try:
        claims = decode_refresh_token(settings, refresh_token)
    except AuthenticationError:
        return
    await set_tenant_guc(session, claims.tenant_id)
    await _revoke(session, claims, reason="logout")


async def _revoke(session: AsyncSession, claims: TokenClaims, *, reason: str) -> None:
    if claims.jti is None:
        return
    session.add(
        RefreshTokenRevocation(
            tenant_id=claims.tenant_id,
            jti=claims.jti,
            user_id=claims.subject,
            expires_at=claims.expires_at,
            reason=reason,
        )
    )


def _issue(settings: Settings, user: User) -> IssuedTokens:
    access, _ = create_access_token(
        settings, user_id=user.id, tenant_id=user.tenant_id, role=user.role
    )
    refresh, _, _ = create_refresh_token(
        settings, user_id=user.id, tenant_id=user.tenant_id, role=user.role
    )
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=access_token_ttl_seconds(settings),
    )


def _dummy_verify(password: str) -> None:
    """Burn comparable time to a real bcrypt check.

    Uses a valid-format hash of a value nobody knows, so the comparison runs the
    full bcrypt cost and the timing of a missing account is not distinctive.
    """
    verify_password(password, "$2b$12$" + "x" * 22 + "y" * 31)


def _utcnow() -> datetime:
    """Timezone-aware now. Naive datetimes are a recurring source of ordering bugs."""
    return datetime.now(UTC)


def tenant_scope_of(tenant_id: uuid.UUID) -> TenantScope:
    """Small helper so repositories read naturally at call sites."""
    return TenantScope(tenant_id=tenant_id)


def validate_uuid(value: str) -> uuid.UUID:
    """Parse a UUID, raising a domain error rather than a ValueError."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("The provided identifier is not valid.") from exc
