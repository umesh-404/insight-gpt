"""Anonymous rate limiting on ``/auth/login``.

``/login`` is reachable without a token, so its limiter must key by client IP
and must never turn a missing bearer token into a 401 — an earlier wiring of
``rate_limit("login")`` did exactly that and broke every authenticated flow in
the suite. These tests pin that behaviour down.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.main import app  # noqa: E402

CREDENTIALS = {"email": "admin@insightgpt.dev", "password": "admin-pass"}


def test_login_works_without_a_bearer_token() -> None:
    """The limiter must not require auth on an anonymous endpoint."""
    with TestClient(app) as client:
        res = client.post("/api/v1/auth/login", json=CREDENTIALS)
    assert res.status_code == 200, res.text
    assert res.json()["access_token"]


def test_login_is_rate_limited_by_ip(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_LOGIN", "2/60")
    deps.reset_rate_limiter()
    try:
        with TestClient(app) as client:
            first = client.post("/api/v1/auth/login", json=CREDENTIALS)
            second = client.post("/api/v1/auth/login", json=CREDENTIALS)
            third = client.post("/api/v1/auth/login", json=CREDENTIALS)

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.headers.get("Retry-After")
        assert third.json()["error"]["code"] == "rate_limited"
    finally:
        deps.reset_rate_limiter()


def test_bad_credentials_still_return_401_not_429(monkeypatch) -> None:
    """A wrong password must fail authentication, not the limiter."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_LOGIN", "5/60")
    deps.reset_rate_limiter()
    try:
        with TestClient(app) as client:
            res = client.post(
                "/api/v1/auth/login",
                json={"email": CREDENTIALS["email"], "password": "wrong"},
            )
        assert res.status_code == 401
    finally:
        deps.reset_rate_limiter()
