"""Roles and the ``require_role`` dependency (doc 06 §2.2, §2.3).

Roles are additive in capability (``viewer`` < ``analyst`` < ``admin``) and
modeled as an ordered ``IntEnum`` so the gate is a single comparison. The
dependency decodes the bearer token, validates it, and compares the caller's
role against the endpoint's minimum. Failures are uniform: 401 for a bad token,
403 for a valid token with an insufficient role.
"""

from __future__ import annotations

from enum import IntEnum

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..api.errors import ForbiddenError, UnauthorizedError
from .tokens import TokenClaims, TokenError, decode_token


class Role(IntEnum):
    viewer = 1
    analyst = 2
    admin = 3

    @classmethod
    def parse(cls, name: str) -> Role:
        try:
            return cls[name]
        except KeyError as exc:
            raise ValueError(f"unknown role {name!r}") from exc


# ``auto_error=False`` so a missing header raises our envelope, not FastAPI's.
_bearer = HTTPBearer(auto_error=False)


async def current_claims(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenClaims:
    if creds is None or not creds.credentials:
        raise UnauthorizedError("Missing bearer token.")
    try:
        claims = decode_token(creds.credentials, expected_type="access")
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    # Stamp identity onto request state so the logging middleware can record it.
    request.state.user_id = claims.sub
    request.state.role = claims.role
    return claims


async def optional_claims(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenClaims | None:
    """Resolve claims when a valid token is present, else ``None``.

    For endpoints that are reachable anonymously (``/auth/login``) but still
    want per-caller behaviour when a token happens to be supplied. Never raises
    on a missing or invalid token — authentication is not the point here.
    """
    if creds is None or not creds.credentials:
        return None
    try:
        claims = decode_token(creds.credentials, expected_type="access")
    except TokenError:
        return None
    request.state.user_id = claims.sub
    request.state.role = claims.role
    return claims


def require_role(minimum: Role):
    """Return a dependency that enforces a minimum role on an endpoint."""

    async def dep(claims: TokenClaims = Depends(current_claims)) -> TokenClaims:
        if Role.parse(claims.role) < minimum:
            raise ForbiddenError(
                "Your role does not permit this action.",
                details={"required": minimum.name, "actual": claims.role},
            )
        return claims

    return dep
