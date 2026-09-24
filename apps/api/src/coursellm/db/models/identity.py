"""Identity models: tenants, users and refresh-token revocations."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coursellm.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)


class UserRole(StrEnum):
    """Coarse authorisation role within a tenant.

    Deliberately small. Roles gate *administrative* actions (inviting members,
    managing the catalogue), not access to learning content, which is scoped by
    ownership rather than by role.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TenantPlan(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenancy boundary: a university, department, cohort or organisation.

    This is the one table outside the Row-Level Security boundary. A policy on
    ``tenants`` would make tenant creation impossible, since creating the first
    row requires already being scoped to a tenant. Access is therefore governed
    by the repository layer: a principal may read only the tenant named in its
    own token.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Login is by email alone, so a tenant slug is not an authentication input.
    # It exists for URLs and for support, not as a security boundary.
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    plan: Mapped[TenantPlan] = mapped_column(
        pg_enum(TenantPlan),
        nullable=False,
        default=TenantPlan.FREE,
        server_default=TenantPlan.FREE.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    users: Mapped[list[User]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<Tenant {self.slug}>"


class User(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A person within a tenant.

    ``email`` is globally unique rather than unique per tenant. That is a real
    product decision with a real consequence: one person belongs to exactly one
    tenant, which makes login a single-step operation and removes the need for a
    workspace selector. Growth beyond that (a student at two universities)
    requires a membership join table, which is a deliberate later migration
    rather than an unplanned complication now.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_tenant_id_role", "tenant_id", "role"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Stored as a normalised (lowercased, stripped) value so that lookups cannot
    # miss because of casing. The original casing is not retained; it carries no
    # information the system needs.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole),
        nullable=False,
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="users", lazy="raise")

    def __repr__(self) -> str:
        # Never include the hash or the email: reprs end up in logs and tracebacks.
        return f"<User {self.id}>"


class RefreshTokenRevocation(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Denylist for refresh tokens.

    Refresh tokens are stateless JWTs. Statelessness keeps the refresh path free
    of a mandatory database round trip on the happy path, but it removes the
    ability to revoke. This table restores revocation for the cases that matter
    (logout, password change, suspected compromise, account deactivation) while
    keeping the common case a signature check.

    The row is keyed by the token's ``jti`` claim, so revocation is an exact
    lookup on a unique index. Rows are only meaningful until the token would
    have expired anyway, which bounds table growth.
    """

    __tablename__ = "refresh_token_revocations"
    __table_args__ = (
        UniqueConstraint("jti", name="uq_refresh_token_revocations_jti"),
        Index("ix_refresh_token_revocations_expires_at", "expires_at"),
    )

    jti: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="logout")

    def __repr__(self) -> str:
        return f"<RefreshTokenRevocation {self.jti}>"
