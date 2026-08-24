"""Auth endpoints: login, refresh, logout, me (doc 06 §2.1).

Login exchanges demo credentials for a ``TokenPair``. Refresh exchanges a valid
refresh token for a new access token; a ``jti`` denylist revokes refresh tokens
on logout. All state here is in-memory (demo scope).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth.roles import Role, current_claims
from ...auth.store import User, authenticate, get_user
from ...auth.tokens import (
    ACCESS_TTL_SECONDS,
    TokenClaims,
    TokenError,
    TokenPair,
    decode_token,
    mint_access_token,
)
from ..errors import UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory denylist of revoked refresh-token ids (doc 06 §2.1).
_REVOKED_JTI: set[str] = set()


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest) -> TokenPair:
    user = authenticate(body.email, body.password)
    if user is None:
        raise UnauthorizedError("Invalid email or password.")
    # Validate the seeded role maps onto a known role.
    Role.parse(user.role)
    return _mint_pair(user)


@router.post("/refresh", response_model=AccessToken)
async def refresh(body: RefreshRequest) -> AccessToken:
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    if claims.jti in _REVOKED_JTI:
        raise UnauthorizedError("This refresh token has been revoked.")
    return AccessToken(
        access_token=mint_access_token(claims.sub, claims.role),
        expires_in=ACCESS_TTL_SECONDS,
    )


@router.post("/logout", status_code=200)
async def logout(body: RefreshRequest) -> dict[str, str]:
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    _REVOKED_JTI.add(claims.jti)
    return {"status": "revoked"}


@router.get("/me", response_model=User)
async def me(claims: TokenClaims = Depends(current_claims)) -> User:
    user = get_user(claims.sub)
    if user is None:
        raise UnauthorizedError("Unknown user.")
    return user


def _mint_pair(user: User) -> TokenPair:
    from ...auth.tokens import mint_token_pair

    return mint_token_pair(user.id, user.role)
