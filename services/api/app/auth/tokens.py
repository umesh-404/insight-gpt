"""JWT access + refresh tokens (doc 06 §2.1).

``JWT_SECRET`` comes from the environment — never the repo.

A development-only fallback secret exists so an offline demo can boot without a
.env, but it is *opt-in*: it applies only when ``APP_ENV`` explicitly names a
non-production environment (or the process is running under the test suite).
With ``APP_ENV`` unset, a missing, placeholder, or too-short ``JWT_SECRET``
raises :class:`JWTSecretError` instead of signing tokens with a value that is
printed in the public repository — otherwise anyone holding the repo can mint an
admin token against a deployed stack.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from pydantic import BaseModel, ValidationError

log = logging.getLogger("insightgpt.auth")

_ALGORITHM = "HS256"
_DEV_SECRET = "dev-insecure-secret-change-me-32b"  # noqa: S105 (documented dev fallback)

# Lifetimes (doc 06 §2.1). Overridable via env for tests/tuning.
ACCESS_TTL_SECONDS = int(os.getenv("JWT_ACCESS_TTL", str(15 * 60)))
REFRESH_TTL_SECONDS = int(os.getenv("JWT_REFRESH_TTL", str(7 * 24 * 60 * 60)))


#: Minimum accepted secret length, in bytes, for HS256.
MIN_SECRET_BYTES = 32

#: Values that are published somewhere (this file, .env.example, the compose
#: default) and therefore carry no secrecy at all.
_PLACEHOLDER_SECRETS = frozenset(
    {
        _DEV_SECRET,
        "change-me-in-.env",
        "change-me",
        "changeme",
        "secret",
        "your-secret-here",
        "dev",
        "test",
    }
)

#: ``APP_ENV`` values that opt in to the insecure development fallback.
_DEV_ENVS = frozenset({"dev", "development", "local", "test", "testing"})


class JWTSecretError(RuntimeError):
    """Raised when ``JWT_SECRET`` is unusable and no dev fallback is permitted."""


def _under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ


def dev_fallback_allowed() -> bool:
    """Whether the insecure development secret may be used.

    Deliberately *not* permissive by default: the fallback requires an explicit
    ``APP_ENV``, so a deployment that simply forgot to set ``JWT_SECRET`` fails
    loudly rather than booting with a secret that is in the public repo.
    """
    return os.getenv("APP_ENV", "").strip().lower() in _DEV_ENVS or _under_pytest()


def _reject(reason: str) -> None:
    raise JWTSecretError(
        f"JWT_SECRET {reason}. Set it to a random value of at least "
        f"{MIN_SECRET_BYTES} bytes (e.g. `openssl rand -hex 32`), or set "
        "APP_ENV=development to allow the insecure development fallback."
    )


def verify_jwt_secret() -> None:
    """Validate ``JWT_SECRET`` at startup so a bad config fails before serving."""
    _secret()


def _secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if not secret or secret.lower() in _PLACEHOLDER_SECRETS:
        if not dev_fallback_allowed():
            _reject("is unset or is a known placeholder value")
        log.warning("JWT_SECRET is not set; using an insecure development secret")
        return _DEV_SECRET
    # Padding a short secret out to 32 bytes adds no entropy — it only hides a
    # weak key — so a short secret is rejected rather than silently accepted.
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        if not dev_fallback_allowed():
            _reject(f"is shorter than {MIN_SECRET_BYTES} bytes")
        log.warning("JWT_SECRET is shorter than %d bytes; this is insecure", MIN_SECRET_BYTES)
    return secret


class TokenClaims(BaseModel):
    """Decoded, validated claims of an access or refresh token."""

    sub: str
    role: Literal["admin", "analyst", "viewer"]
    jti: str
    iat: int
    exp: int
    typ: Literal["access", "refresh"] = "access"


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # access-token TTL in seconds


def _encode(sub: str, role: str, typ: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "typ": typ,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def mint_token_pair(sub: str, role: str) -> TokenPair:
    return TokenPair(
        access_token=_encode(sub, role, "access", ACCESS_TTL_SECONDS),
        refresh_token=_encode(sub, role, "refresh", REFRESH_TTL_SECONDS),
        expires_in=ACCESS_TTL_SECONDS,
    )


def mint_access_token(sub: str, role: str) -> str:
    return _encode(sub, role, "access", ACCESS_TTL_SECONDS)


class TokenError(ValueError):
    """Raised when a token is missing, expired, malformed, or the wrong type."""


def decode_token(token: str, *, expected_type: str = "access") -> TokenClaims:
    try:
        raw = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token is invalid") from exc
    try:
        claims = TokenClaims(**raw)
    except ValidationError as exc:
        # A correctly signed but structurally wrong token (missing claim, an
        # unknown role) is still an auth failure, not a server error.
        raise TokenError("token claims are malformed") from exc
    if claims.typ != expected_type:
        raise TokenError(f"expected a {expected_type} token, got {claims.typ!r}")
    return claims
