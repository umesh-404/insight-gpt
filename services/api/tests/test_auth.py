"""Auth contract tests — login, the refresh cookie, revocation, role gating.

The browser client posts ``/auth/refresh`` with *no body* and
``credentials: 'include'``; these tests pin that path, the JSON-body fallback
used by non-browser clients, and the guarantees around expiry and logout.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers.auth import REFRESH_COOKIE, REFRESH_COOKIE_PATH, reset_revocations
from app.auth import tokens

ANALYST = {"email": "analyst@insightgpt.dev", "password": "analyst-pass"}
VIEWER = {"email": "viewer@insightgpt.dev", "password": "viewer-pass"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    reset_revocations()
    with TestClient(create_app()) as c:
        yield c
    reset_revocations()


def _login(client: TestClient, creds: dict[str, str] = ANALYST):
    resp = client.post("/api/v1/auth/login", json=creds)
    assert resp.status_code == 200, resp.text
    return resp


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- login ------------------------------------------------------------------


def test_login_returns_pair_and_sets_httponly_cookie(client: TestClient) -> None:
    resp = _login(client)
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] == tokens.ACCESS_TTL_SECONDS

    raw = resp.headers["set-cookie"]
    assert REFRESH_COOKIE in raw
    assert "HttpOnly" in raw
    assert "Path=/api/v1/auth" in raw
    assert "samesite=lax" in raw.lower()
    # COOKIE_SECURE defaults to false so the cookie survives plain-http dev.
    assert "Secure" not in raw
    assert client.cookies.get(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH) == body["refresh_token"]


def test_login_bad_password_is_unauthorized(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/login", json={**ANALYST, "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert "set-cookie" not in resp.headers


# --- refresh ----------------------------------------------------------------


def test_refresh_with_no_body_uses_the_cookie(client: TestClient) -> None:
    """The exact call the browser makes: bodyless POST, cookie only."""
    _login(client)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] and body["token_type"] == "bearer"
    # The refreshed access token is usable.
    me = client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == ANALYST["email"]


def test_refresh_rewrites_the_cookie(client: TestClient) -> None:
    _login(client)
    resp = client.post("/api/v1/auth/refresh")
    assert REFRESH_COOKIE in resp.headers["set-cookie"]


def test_refresh_with_json_body_works_for_non_browser_clients(client: TestClient) -> None:
    refresh_token = _login(client).json()["refresh_token"]
    client.cookies.clear()
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_refresh_without_any_token_is_unauthorized(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_refresh_rejects_an_empty_json_object(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/refresh", json={})
    assert resp.status_code == 401


def test_refresh_after_the_access_token_expires(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale access token 401s, and the cookie alone recovers the session."""
    monkeypatch.setattr(tokens, "ACCESS_TTL_SECONDS", -10)
    expired = _login(client).json()["access_token"]
    monkeypatch.undo()

    stale = client.get("/api/v1/auth/me", headers=_auth(expired))
    assert stale.status_code == 401
    assert "expired" in stale.json()["error"]["message"]

    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200, resp.text
    fresh = resp.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers=_auth(fresh)).status_code == 200


def test_refresh_rejects_an_access_token(client: TestClient) -> None:
    """Token-type confusion: an access token is not a refresh token."""
    access = _login(client).json()["access_token"]
    client.cookies.clear()
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401
    assert "refresh" in resp.json()["error"]["message"]


def test_bearer_rejects_a_refresh_token(client: TestClient) -> None:
    refresh_token = _login(client).json()["refresh_token"]
    resp = client.get("/api/v1/auth/me", headers=_auth(refresh_token))
    assert resp.status_code == 401


def test_malformed_token_claims_are_401_not_500(client: TestClient) -> None:
    """A correctly signed token with an unknown role must not blow up."""
    bad = tokens._encode("u_analyst", "superuser", "access", 60)
    resp = client.get("/api/v1/auth/me", headers=_auth(bad))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


# --- logout -----------------------------------------------------------------


def test_logout_revokes_the_jti_and_clears_the_cookie(client: TestClient) -> None:
    refresh_token = _login(client).json()["refresh_token"]

    out = client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    assert out.json()["status"] == "revoked"
    raw = out.headers["set-cookie"]
    assert REFRESH_COOKIE in raw
    assert "Max-Age=0" in raw or 'igpt_refresh=""' in raw
    assert client.cookies.get(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH) is None

    # The revoked token is dead even when replayed in the body.
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401
    assert "revoked" in replay.json()["error"]["message"]


def test_logout_is_idempotent_without_a_session(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"
    assert REFRESH_COOKIE in resp.headers["set-cookie"]


def test_logout_accepts_a_body_token(client: TestClient) -> None:
    refresh_token = _login(client).json()["refresh_token"]
    client.cookies.clear()
    assert client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    ).json() == {"status": "revoked"}


# --- identity + role gating -------------------------------------------------


def test_me_returns_the_public_user(client: TestClient) -> None:
    access = _login(client).json()["access_token"]
    body = client.get("/api/v1/auth/me", headers=_auth(access)).json()
    assert body == {
        "id": "u_analyst",
        "email": "analyst@insightgpt.dev",
        "role": "analyst",
        "name": "Analyst User",
    }


def test_me_requires_a_bearer_token(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_role_gate_reports_required_and_actual(client: TestClient) -> None:
    access = _login(client, VIEWER).json()["access_token"]
    resp = client.post(
        "/api/v1/metrics/query", json={"metric": "revenue"}, headers=_auth(access)
    )
    assert resp.status_code == 403
    details = resp.json()["error"]["details"]
    assert details == {"required": "analyst", "actual": "viewer"}


def test_cors_allow_list_is_explicit_and_credentialed(client: TestClient) -> None:
    resp = client.get(
        "/health", headers={"Origin": "http://localhost:3020"}
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3020"
    assert resp.headers["access-control-allow-credentials"] == "true"
