"""Security primitives: passwords, tokens, injection detection and output validation.

Nothing here trusts an input. Every function either validates its arguments or
returns a safe value for every failure mode; none of them raise a
provider-specific or library-specific exception to the caller.
"""

from coursellm.security.injection import (
    CLASS_WEIGHTS,
    SIGNAL_CLASSES,
    ClassifierVerdict,
    InjectionVerdict,
    Level,
    Match,
    apply_classifier,
    classify,
    classify_with_classifier,
    level_for_score,
    parse_classifier_payload,
)
from coursellm.security.output import (
    REDACTION_PLACEHOLDER,
    ValidatedOutput,
    contains_secret,
    redact_secrets,
    validate_output,
)
from coursellm.security.passwords import (
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from coursellm.security.ratelimit import (
    InMemoryRateLimiter,
    RateLimitDecision,
    RateLimiter,
    RateLimitExceededError,
    RedisRateLimiter,
    get_rate_limiter,
    reset_rate_limiter,
    set_rate_limiter,
)
from coursellm.security.sanitize import (
    neutralise_markers,
    sanitize_query,
    scrub_for_storage,
    strip_invisibles,
)
from coursellm.security.tokens import (
    TokenClaims,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)

__all__ = [
    "CLASS_WEIGHTS",
    "REDACTION_PLACEHOLDER",
    "SIGNAL_CLASSES",
    "ClassifierVerdict",
    "InMemoryRateLimiter",
    "InjectionVerdict",
    "Level",
    "Match",
    "RateLimitDecision",
    "RateLimitExceededError",
    "RateLimiter",
    "RedisRateLimiter",
    "TokenClaims",
    "ValidatedOutput",
    "apply_classifier",
    "classify",
    "classify_with_classifier",
    "contains_secret",
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "decode_refresh_token",
    "get_rate_limiter",
    "hash_password",
    "level_for_score",
    "needs_rehash",
    "neutralise_markers",
    "parse_classifier_payload",
    "redact_secrets",
    "reset_rate_limiter",
    "sanitize_query",
    "scrub_for_storage",
    "set_rate_limiter",
    "strip_invisibles",
    "validate_output",
    "validate_password_policy",
    "verify_password",
]
