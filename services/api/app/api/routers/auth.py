"""Auth endpoints: login, refresh, logout, me (doc 06 §2.1).

Login exchanges demo credentials for a ``TokenPair`` *and* plants the refresh
token in an httpOnly cookie. Refresh and logout accept the refresh token from
either that cookie (browsers, which send it automatically) or an optional JSON
body (CLI/service clients) — so a bodyless ``POST /auth/refresh`` with
``credentials: 'include'`` is a first-class, supported call. A ``jti`` denylist
revokes refresh tokens on logout. All state here is in-memory (demo scope).
"""

from __future__ import annotations

import os
from collections import OrderedDict

from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel, Field

from ...auth.roles import Role, current_claims
from ...auth.store import User, authenticate, get_user
from ...auth.tokens import (
    ACCESS_TTL_SECONDS,
    REFRESH_TTL_SECONDS,
    TokenClaims,
    TokenError,
    TokenPair,
    decode_token,
    mint_access_token,
    mint_token_pair,
)
from ..deps import rate_limit
from ..errors import UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])

#: Name of the httpOnly cookie carrying the refresh token.
REFRESH_COOKIE = "igpt_refresh"

#: Scoped so the cookie is only ever sent to the auth endpoints that need it.
#: Browsers ignore the port, so localhost:3020 -> localhost:8020 works in dev.
REFRESH_COOKIE_PATH = "/api/v1/auth"

# In-memory denylist of revoked refresh-token ids (doc 06 §2.1). Bounded so a
# long-lived process cannot be grown without limit by repeated logouts.
_MAX_REVOKED = 10_000
_REVOKED_JTI: OrderedDict[str, None] = OrderedDict()


def _cookie_secure() -> bool:
    """``Secure`` flag for the refresh cookie; off by default so localhost works."""
    return os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path=REFRESH_COOKIE_PATH,
        max_age=REFRESH_TTL_SECONDS,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )


def _revoke(jti: str) -> None:
    _REVOKED_JTI[jti] = None
    _REVOKED_JTI.move_to_end(jti)
    while len(_REVOKED_JTI) > _MAX_REVOKED:
        _REVOKED_JTI.popitem(last=False)


def reset_revocations() -> None:
    """Clear the denylist (used by tests)."""
    _REVOKED_JTI.clear()


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    """Optional fallback body for non-browser clients.

    Both the model and its field are optional so ``POST`` with no body at all —
    what the browser client sends, relying on the cookie — validates cleanly
    instead of failing with a 422.
    """

    refresh_token: str | None = None


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _refresh_claims(cookie_token: str | None, body: RefreshRequest | None) -> TokenClaims:
    """Resolve and validate the refresh token from the cookie, else the body."""
    token = (cookie_token or "").strip() or ((body.refresh_token or "").strip() if body else "")
    if not token:
        raise UnauthorizedError("Missing refresh token.")
    try:
        claims = decode_token(token, expected_type="refresh")
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    if claims.jti in _REVOKED_JTI:
        raise UnauthorizedError("This refresh token has been revoked.")
    return claims


@router.post(
    "/login",
    response_model=TokenPair,
    # Login is reachable anonymously, so the limiter keys by client IP.
    dependencies=[Depends(rate_limit("login", require_auth=False))],
)
async def login(body: LoginRequest, response: Response) -> TokenPair:
    user = authenticate(body.email, body.password)
    if user is None:
        raise UnauthorizedError("Invalid email or password.")
    # Validate the seeded role maps onto a known role.
    Role.parse(user.role)
    pair = mint_token_pair(user.id, user.role)
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/refresh", response_model=AccessToken)
async def refresh(
    response: Response,
    body: RefreshRequest | None = None,
    igpt_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> AccessToken:
    claims = _refresh_claims(igpt_refresh, body)
    # Re-stamp the cookie so an active session keeps a full-length refresh window.
    token = igpt_refresh or (body.refresh_token if body and body.refresh_token else None)
    if token:
        _set_refresh_cookie(response, token)
    return AccessToken(
        access_token=mint_access_token(claims.sub, claims.role),
        expires_in=ACCESS_TTL_SECONDS,
    )


@router.post("/logout", status_code=200)
async def logout(
    response: Response,
    body: RefreshRequest | None = None,
    igpt_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> dict[str, str]:
    # Logout is idempotent: the cookie is always cleared, and an absent or
    # already-invalid token is not an error — the caller's goal (no usable
    # session) is met either way, so the client never has to handle a 401 here.
    _clear_refresh_cookie(response)
    try:
        claims = _refresh_claims(igpt_refresh, body)
    except UnauthorizedError:
        return {"status": "cleared"}
    _revoke(claims.jti)
    return {"status": "revoked"}


@router.get("/me", response_model=User)
async def me(claims: TokenClaims = Depends(current_claims)) -> User:
    user = get_user(claims.sub)
    if user is None:
        raise UnauthorizedError("Unknown user.")
    return user
