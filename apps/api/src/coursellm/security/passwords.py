"""Password hashing and password policy.

bcrypt with an explicit cost factor. Two subtleties are handled rather than
inherited:

* **bcrypt truncates silently at 72 bytes.** A long passphrase is not stronger
  than its first 72 bytes, and two different long passwords sharing a 72-byte
  prefix are interchangeable. Rather than accept that quietly, an over-long
  password is rejected at the boundary.
* **Verification is constant-time with respect to the stored hash** because
  ``bcrypt.checkpw`` is used directly, and every failure mode returns ``False``
  rather than raising, so a malformed stored hash cannot be distinguished from a
  wrong password by an attacker measuring responses.
"""

from __future__ import annotations

import bcrypt

from coursellm.core.errors import ValidationError

# Cost factor. 12 is ~4x the work of the bcrypt default of 10 and remains
# well under 250 ms on current hardware; raise it only alongside a plan to
# rehash on next successful login.
BCRYPT_ROUNDS = 12

# bcrypt operates on at most 72 bytes. Enforced rather than truncated.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8

# Rejected outright. This is not a substitute for a breached-password check,
# which belongs at the identity-provider layer, but it removes the worst
# offenders and makes the policy explicit.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "qwertyui",
        "qwerty123",
        "letmein1",
        "iloveyou",
        "admin123",
        "welcome1",
        "changeme",
        "passw0rd",
        "football",
        "baseball",
        "sunshine",
        "trustno1",
        "coursellm",
    }
)


def validate_password_policy(password: str) -> None:
    """Reject long, short and obviously-common passwords.

    Raises :class:`ValidationError` with a message that states the rule but never
    echoes the password.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            fields={"password": "too_short"},
        )

    encoded_length = len(password.encode("utf-8"))
    if encoded_length > MAX_PASSWORD_BYTES:
        # Rejected, not truncated: silent truncation would make the effective
        # password shorter than the user believes.
        raise ValidationError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes when UTF-8 encoded "
            f"(received {encoded_length}).",
            fields={"password": "too_long"},
        )

    if password.lower() in _COMMON_PASSWORDS:
        raise ValidationError(
            "That password is too common. Choose something less predictable.",
            fields={"password": "too_common"},
        )


def hash_password(password: str) -> str:
    """Hash a password for storage. The policy is enforced here, not by callers."""
    validate_password_policy(password)
    digest: bytes = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return digest.decode("ascii")


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a password against a stored hash.

    Returns ``False`` for every failure mode, including a malformed or truncated
    stored hash. It never raises and never logs, so a caller cannot accidentally
    turn a storage problem into a 500 that reveals the difference between "no
    such user" and "bad password".
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Whether a stored hash was produced with a weaker cost than the current one.

    Enables a cost-factor increase without a forced password reset: on the next
    successful login the hash is upgraded transparently.
    """
    try:
        # Format: $2b$<rounds>$<22-char salt><31-char digest>
        parts = hashed_password.split("$")
        if len(parts) < 4:
            return True
        return int(parts[2]) < BCRYPT_ROUNDS
    except (IndexError, ValueError):
        return True
