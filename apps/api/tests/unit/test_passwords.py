"""Password hashing and policy tests."""

from __future__ import annotations

import pytest

from coursellm.core.errors import ValidationError
from coursellm.security.passwords import (
    BCRYPT_ROUNDS,
    MAX_PASSWORD_BYTES,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)

pytestmark = pytest.mark.unit


class TestHashing:
    def test_roundtrip(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed)

    def test_wrong_password_is_rejected(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert not verify_password("correct horse battery stapl", hashed)

    def test_same_password_hashes_differently(self) -> None:
        """Per-hash salt: two users with the same password must not share a hash."""
        assert hash_password("a-perfectly-fine-password") != hash_password(
            "a-perfectly-fine-password"
        )

    def test_hash_never_contains_the_password(self) -> None:
        hashed = hash_password("a-perfectly-fine-password")
        assert "a-perfectly-fine-password" not in hashed

    def test_cost_factor_is_explicit(self) -> None:
        hashed = hash_password("a-perfectly-fine-password")
        assert hashed.startswith(f"$2b${BCRYPT_ROUNDS}$")


class TestPolicy:
    def test_short_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least"):
            validate_password_policy("short")

    def test_common_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="too common"):
            validate_password_policy("password123")

    def test_common_password_is_matched_case_insensitively(self) -> None:
        with pytest.raises(ValidationError, match="too common"):
            validate_password_policy("PassWord123")

    def test_acceptable_password_passes(self) -> None:
        validate_password_policy("a-perfectly-fine-password")


class TestBcryptTruncation:
    """bcrypt silently ignores everything past 72 bytes.

    Two different long passwords sharing a 72-byte prefix would therefore be
    interchangeable. Accepting that quietly would make the effective password
    shorter than the user believes, so it is rejected at the boundary instead.
    """

    def test_overlong_password_is_rejected_not_truncated(self) -> None:
        too_long = "a" * (MAX_PASSWORD_BYTES + 1)
        with pytest.raises(ValidationError, match="at most"):
            validate_password_policy(too_long)

    def test_exactly_at_the_limit_is_accepted(self) -> None:
        validate_password_policy("a" * MAX_PASSWORD_BYTES)

    def test_the_limit_is_measured_in_bytes_not_characters(self) -> None:
        """Multi-byte characters consume more than one byte each."""
        # 40 three-byte characters = 120 bytes, well past the limit, yet only
        # 40 characters long. A character-based check would let it through.
        multibyte = "\u4e2d" * 40
        assert len(multibyte) < MAX_PASSWORD_BYTES
        with pytest.raises(ValidationError, match="at most"):
            validate_password_policy(multibyte)

    def test_hash_password_enforces_the_policy(self) -> None:
        """Policy is not the caller's responsibility."""
        with pytest.raises(ValidationError):
            hash_password("short")


class TestVerificationRobustness:
    @pytest.mark.parametrize(
        "malformed",
        [
            "",
            "not-a-hash",
            "$2b$12$tooshort",
            "$2b$12$" + "x" * 100,
            "plaintext",
        ],
    )
    def test_malformed_hash_returns_false_and_does_not_raise(self, malformed: str) -> None:
        """A storage problem must not become a 500 that reveals account state."""
        assert verify_password("anything", malformed) is False

    def test_empty_password_does_not_raise(self) -> None:
        assert verify_password("", hash_password("a-perfectly-fine-password")) is False


class TestRehash:
    def test_current_cost_does_not_need_rehash(self) -> None:
        assert needs_rehash(hash_password("a-perfectly-fine-password")) is False

    def test_lower_cost_needs_rehash(self) -> None:
        """Enables raising the cost factor without forcing a password reset."""
        weaker = "$2b$04$" + hash_password("a-perfectly-fine-password").split("$", 3)[3]
        assert needs_rehash(weaker) is True

    @pytest.mark.parametrize("malformed", ["", "garbage", "$2b$notanumber$abc"])
    def test_malformed_hash_is_treated_as_needing_rehash(self, malformed: str) -> None:
        assert needs_rehash(malformed) is True
