"""JWT issuing and verification tests.

Each test below corresponds to a claim or verification option in
:mod:`coursellm.security.tokens`; removing the option would make the matching test
fail, which is the point.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import AuthenticationError
from coursellm.db.models.identity import UserRole
from coursellm.security.tokens import (
    TOKEN_AUDIENCE,
    TOKEN_ISSUER,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)

pytestmark = pytest.mark.unit

USER_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class TestRoundTrip:
    def test_access_token_claims(self, test_settings: Settings) -> None:
        token, expires_at = create_access_token(
            test_settings, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.MEMBER
        )
        claims = decode_access_token(test_settings, token)

        assert claims.subject == USER_ID
        assert claims.tenant_id == TENANT_ID
        assert claims.role is UserRole.MEMBER
        assert claims.token_type == "access"
        assert claims.expires_at > datetime.now(UTC)
        assert abs((claims.expires_at - expires_at).total_seconds()) < 2

    def test_refresh_token_carries_a_jti(self, test_settings: Settings) -> None:
        """The jti is what makes a stateless refresh token revocable."""
        token, _, jti = create_refresh_token(
            test_settings, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.OWNER
        )
        claims = decode_refresh_token(test_settings, token)
        assert claims.jti == jti
        assert claims.token_type == "refresh"

    def test_access_token_has_no_jti(self, test_settings: Settings) -> None:
        token, _ = create_access_token(
            test_settings, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.MEMBER
        )
        assert decode_access_token(test_settings, token).jti is None


class TestTokenTypeConfusion:
    """Both token types are signed with the same key.

    Without the ``typ`` claim, a refresh token — which is long-lived and stored
    differently by clients — would be accepted as an access token. That is a
    privilege escalation with a trivial exploit, so it is tested directly.
    """

    def test_refresh_token_is_rejected_as_an_access_token(self, test_settings: Settings) -> None:
        refresh, _, _ = create_refresh_token(
            test_settings, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.MEMBER
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, refresh)

    def test_access_token_is_rejected_as_a_refresh_token(self, test_settings: Settings) -> None:
        access, _ = create_access_token(
            test_settings, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.MEMBER
        )
        with pytest.raises(AuthenticationError):
            decode_refresh_token(test_settings, access)


class TestSignature:
    def test_tampered_payload_is_rejected(self, test_settings: Settings) -> None:
        token, _ = create_access_token(
            test_settings, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.MEMBER
        )
        # Re-encode with a different tenant but keep the original signature.
        header, _original_payload, signature = token.split(".")
        forged_payload = jwt.utils.base64url_encode(
            b'{"sub":"aaaaaaaa-0000-0000-0000-000000000001",'
            b'"tid":"22222222-2222-2222-2222-222222222222","role":"owner",'
            b'"typ":"access","iat":0,"exp":9999999999,"iss":"coursellm",'
            b'"aud":"coursellm-api"}'
        ).decode()
        forged = f"{header}.{forged_payload}.{signature}"

        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, forged)

    def test_token_signed_with_another_key_is_rejected(self, test_settings: Settings) -> None:
        # Long enough that PyJWT does not warn; the point is that it differs.
        other = test_settings.model_copy(
            update={"secret_key": "a-completely-different-key-" + "x" * 40}
        )
        token, _ = create_access_token(
            other, user_id=USER_ID, tenant_id=TENANT_ID, role=UserRole.MEMBER
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)

    def test_unsigned_token_is_rejected(self, test_settings: Settings) -> None:
        """The ``alg: none`` attack."""
        header = jwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
        payload = jwt.utils.base64url_encode(
            b'{"sub":"aaaaaaaa-0000-0000-0000-000000000001",'
            b'"tid":"11111111-1111-1111-1111-111111111111","role":"owner",'
            b'"typ":"access","iat":0,"exp":9999999999,"iss":"coursellm",'
            b'"aud":"coursellm-api"}'
        ).decode()
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, f"{header}.{payload}.")

    @pytest.mark.parametrize("garbage", ["", "not.a.token", "a.b", "....", "a.b.c.d"])
    def test_malformed_tokens_are_rejected(self, test_settings: Settings, garbage: str) -> None:
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, garbage)


class TestIssuerAndAudience:
    """Both are verified, so a token minted for another service is not usable."""

    def _mint(self, settings: Settings, **overrides: object) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(USER_ID),
            "tid": str(TENANT_ID),
            "role": "member",
            "typ": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        }
        payload.update(overrides)
        return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    def test_wrong_issuer_is_rejected(self, test_settings: Settings) -> None:
        token = self._mint(test_settings, iss="some-other-service")
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)

    def test_wrong_audience_is_rejected(self, test_settings: Settings) -> None:
        token = self._mint(test_settings, aud="some-other-api")
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)


class TestExpiry:
    def test_expired_token_is_rejected(self, test_settings: Settings) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        token = jwt.encode(
            {
                "sub": str(USER_ID),
                "tid": str(TENANT_ID),
                "role": "member",
                "typ": "access",
                "iat": int(past.timestamp()),
                "exp": int((past + timedelta(minutes=1)).timestamp()),
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
            },
            test_settings.secret_key,
            algorithm=test_settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)

    def test_expiry_error_is_distinguishable_to_the_user(self, test_settings: Settings) -> None:
        """An expired session should say so; the message is not a security leak."""
        past = datetime.now(UTC) - timedelta(hours=2)
        token = jwt.encode(
            {
                "sub": str(USER_ID),
                "tid": str(TENANT_ID),
                "role": "member",
                "typ": "access",
                "iat": int(past.timestamp()),
                "exp": int((past + timedelta(minutes=1)).timestamp()),
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
            },
            test_settings.secret_key,
            algorithm=test_settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError, match="expired"):
            decode_access_token(test_settings, token)


class TestRequiredClaims:
    @pytest.mark.parametrize("missing", ["sub", "tid", "typ", "exp", "iat"])
    def test_missing_required_claim_is_rejected(
        self, test_settings: Settings, missing: str
    ) -> None:
        now = datetime.now(UTC)
        payload = {
            "sub": str(USER_ID),
            "tid": str(TENANT_ID),
            "role": "member",
            "typ": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        }
        del payload[missing]
        token = jwt.encode(payload, test_settings.secret_key, algorithm=test_settings.jwt_algorithm)
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)

    def test_non_uuid_subject_is_rejected(self, test_settings: Settings) -> None:
        """A string subject would reach a policy comparison as a type error."""
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "not-a-uuid",
                "tid": str(TENANT_ID),
                "role": "member",
                "typ": "access",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
            },
            test_settings.secret_key,
            algorithm=test_settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)

    def test_unknown_role_is_rejected(self, test_settings: Settings) -> None:
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(USER_ID),
                "tid": str(TENANT_ID),
                "role": "superuser",
                "typ": "access",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
            },
            test_settings.secret_key,
            algorithm=test_settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)


class TestAlgorithmPinning:
    def test_algorithm_cannot_be_downgraded_by_the_token_header(
        self, test_settings: Settings
    ) -> None:
        """Algorithm confusion: the verifier must not honour the token's header.

        The decoder is given an explicit algorithm list derived from
        configuration, so a token claiming a different algorithm fails before the
        signature is even considered.
        """
        token = jwt.encode(
            {
                "sub": str(USER_ID),
                "tid": str(TENANT_ID),
                "role": "member",
                "typ": "access",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
            },
            test_settings.secret_key,
            algorithm="HS512",
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(test_settings, token)
