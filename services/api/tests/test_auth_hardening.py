"""Two auth weaknesses that the contract tests in test_auth.py do not cover.

1. ``JWT_SECRET`` used to fall back to a secret literal that is printed in this
   repository, with only a ``log.warning``, and compose shipped a published
   default (``change-me-in-.env``). Anyone with the public repo could mint an
   admin token against a deployed stack. A teammate's ``ljust(32, "-")`` padding
   made it worse, not better: it adds no entropy and silently accepts a weak
   key.
2. ``/auth/refresh`` minted an access token from the *refresh* token's ``role``
   claim. A refresh token lives seven days, so a demotion or a deactivation was
   unenforceable for a week.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-32-bytes")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers.auth import REFRESH_COOKIE, reset_revocations
from app.auth import store, tokens

ANALYST = {"email": "analyst@insightgpt.dev", "password": "analyst-pass"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    reset_revocations()
    with TestClient(create_app()) as c:
        yield c
    reset_revocations()


# --------------------------------------------------------------------------- #
# JWT_SECRET must not fall back silently outside an explicit dev environment   #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that is not a dev box and not the test suite.

    ``PYTEST_CURRENT_TEST`` is re-set by pytest for every test, so the
    under-pytest probe is stubbed rather than the environment scrubbed.
    """
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(tokens, "_under_pytest", lambda: False)


@pytest.mark.parametrize(
    "value",
    [
        None,                          # unset entirely
        "",                            # shipped empty by .env.example
        "change-me-in-.env",           # the compose default
        "dev-insecure-secret-change-me-32b",  # the in-repo dev fallback itself
        "short-secret",                # under 32 bytes: padding added no entropy
    ],
)
def test_unusable_secret_is_refused_outside_a_dev_environment(
    value: str | None, production_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", value)
    with pytest.raises(tokens.JWTSecretError):
        tokens.verify_jwt_secret()


def test_startup_refuses_to_build_the_app_with_an_unusable_secret(
    production_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(tokens.JWTSecretError):
        create_app()


def test_dev_fallback_is_opt_in_via_app_env(
    production_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    tokens.verify_jwt_secret()  # must not raise
    assert tokens.dev_fallback_allowed() is True


def test_a_real_secret_is_accepted_without_a_dev_flag(
    production_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", "a" * 64)
    tokens.verify_jwt_secret()
    assert tokens.dev_fallback_allowed() is False


def test_a_short_secret_is_not_padded_into_acceptability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Padding to 32 bytes must not change the key that gets used."""
    monkeypatch.setenv("JWT_SECRET", "x" * 10)
    assert tokens._secret() == "x" * 10


# --------------------------------------------------------------------------- #
# /auth/refresh must re-read the user, not trust the refresh token's role      #
# --------------------------------------------------------------------------- #
def test_refresh_mints_the_current_role_not_the_token_s_stale_role(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = client.post("/api/v1/auth/login", json=ANALYST)
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refresh_token"]
    assert tokens.decode_token(refresh_token, expected_type="refresh").role == "analyst"

    # The user is demoted after the refresh token was issued.
    real_get_user = store.get_user

    def demoted(user_id: str):
        user = real_get_user(user_id)
        if user is not None and user.role == "analyst":
            return user.model_copy(update={"role": "viewer"})
        return user

    monkeypatch.setattr("app.api.routers.auth.get_user", demoted)

    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200, resp.text
    claims = tokens.decode_token(resp.json()["access_token"])
    assert claims.role == "viewer", (
        "refresh copied the role out of a 7-day refresh token, so the demotion "
        "would not take effect for a week"
    )


def test_refresh_rejects_a_token_for_a_user_who_no_longer_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = client.post("/api/v1/auth/login", json=ANALYST)
    assert login.status_code == 200, login.text
    monkeypatch.setattr("app.api.routers.auth.get_user", lambda _uid: None)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401, resp.text


def test_refresh_is_rate_limited_like_login() -> None:
    """A route reachable with only a cookie needs the same budget as login."""
    from app.api.routers import auth as auth_router

    routes = {
        r.path: r
        for r in auth_router.router.routes
        if getattr(r, "path", "").endswith(("/refresh", "/login"))
    }
    for path in ("/auth/refresh", "/auth/login"):
        deps = getattr(routes[path], "dependencies", [])
        assert deps, f"{path} has no rate-limit dependency"


def test_refresh_cookie_is_restamped_and_still_works(client: TestClient) -> None:
    # Guards the fix against regressing the browser's bodyless refresh path.
    assert client.post("/api/v1/auth/login", json=ANALYST).status_code == 200
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    assert REFRESH_COOKIE in resp.cookies or REFRESH_COOKIE in client.cookies
