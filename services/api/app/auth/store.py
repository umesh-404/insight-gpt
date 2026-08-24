"""A seeded, in-memory demo user store.

Real deployments back this with Postgres and Argon2 password hashes (doc 06
§2.1). For the offline demo the store is a dict seeded with one user per role,
and passwords are compared against a salted SHA-256 digest (adequate for a
non-secret demo; the production store swaps in Argon2 without touching callers).
"""

from __future__ import annotations

import hashlib
import hmac
import os

from pydantic import BaseModel

_PW_SALT = os.getenv("DEMO_PW_SALT", "insightgpt-demo-salt")


def _hash(password: str) -> str:
    return hashlib.sha256(f"{_PW_SALT}:{password}".encode()).hexdigest()


class User(BaseModel):
    id: str
    email: str
    role: str
    name: str


class _StoredUser(User):
    password_hash: str


# Seeded demo accounts — one per role. Passwords are demo-only, not secrets.
_SEED = [
    ("u_admin", "admin@insightgpt.dev", "admin", "admin-pass", "Admin User"),
    ("u_analyst", "analyst@insightgpt.dev", "analyst", "analyst-pass", "Analyst User"),
    ("u_viewer", "viewer@insightgpt.dev", "viewer", "viewer-pass", "Viewer User"),
]

_USERS: dict[str, _StoredUser] = {
    email: _StoredUser(id=uid, email=email, role=role, name=name, password_hash=_hash(pw))
    for uid, email, role, pw, name in _SEED
}


def _public(u: _StoredUser) -> User:
    return User(id=u.id, email=u.email, role=u.role, name=u.name)


def authenticate(email: str, password: str) -> User | None:
    """Return the user on a correct password, else ``None`` (constant-time compare)."""
    stored = _USERS.get(email.lower().strip())
    if stored is None:
        # Still compute a hash to keep timing roughly uniform for unknown emails.
        _hash(password)
        return None
    if not hmac.compare_digest(stored.password_hash, _hash(password)):
        return None
    return _public(stored)


def get_user(user_id: str) -> User | None:
    for u in _USERS.values():
        if u.id == user_id:
            return _public(u)
    return None
