"""Security primitives: password hashing, token issuing, injection detection.

Nothing here trusts an input. Every function either validates its arguments or
returns a safe value for every failure mode; none of them raise a
provider-specific or library-specific exception to the caller.
"""

from coursellm.security.passwords import (
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from coursellm.security.tokens import (
    TokenClaims,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)

__all__ = [
    "TokenClaims",
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "decode_refresh_token",
    "hash_password",
    "needs_rehash",
    "validate_password_policy",
    "verify_password",
]
