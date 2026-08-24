"""Authentication and authorization: JWT tokens, roles, and the demo user store.

The API is stateless (doc 06 §1.6): a bearer access token carries the caller's
identity and role; a longer-lived refresh token mints new access tokens. Roles
are an *ordered* enum so ``require_role`` is a single comparison, not scattered
``if`` checks (doc 06 §2.3).
"""

from __future__ import annotations

from .roles import Role, current_claims, require_role
from .tokens import TokenClaims, TokenPair, decode_token, mint_token_pair

__all__ = [
    "Role",
    "TokenClaims",
    "TokenPair",
    "current_claims",
    "decode_token",
    "mint_token_pair",
    "require_role",
]
