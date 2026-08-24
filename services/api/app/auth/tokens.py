"""JWT access + refresh tokens (doc 06 §2.1).

``JWT_SECRET`` comes from the environment — never the repo. For an offline demo
run a development-only fallback secret is used so the server still boots, but a
warning is emitted; production must set the variable.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from pydantic import BaseModel

log = logging.getLogger("insightgpt.auth")

_ALGORITHM = "HS256"
_DEV_SECRET = "dev-insecure-secret-change-me"  # noqa: S105 (documented dev fallback)

# Lifetimes (doc 06 §2.1). Overridable via env for tests/tuning.
ACCESS_TTL_SECONDS = int(os.getenv("JWT_ACCESS_TTL", str(15 * 60)))
REFRESH_TTL_SECONDS = int(os.getenv("JWT_REFRESH_TTL", str(7 * 24 * 60 * 60)))


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        log.warning("JWT_SECRET is not set; using an insecure development secret")
        return _DEV_SECRET
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
    claims = TokenClaims(**raw)
    if claims.typ != expected_type:
        raise TokenError(f"expected a {expected_type} token, got {claims.typ!r}")
    return claims
