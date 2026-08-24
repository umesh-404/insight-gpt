"""API surface tests — fully offline (fixture engine + fake provider).

Exercises the contract paths that matter: health, login -> ask (non-stream)
happy path, role gating, and the governed metric query.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

import pytest
from fastapi.testclient import TestClient

from app.api.deps import reset_caches
from app.api.main import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    return TestClient(create_app())


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "X-Request-ID" in resp.headers


def test_login_bad_password_is_unauthorized(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@insightgpt.dev", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_ask_non_stream_hybrid_cited_answer(client: TestClient) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask",
        json={"question": "Why did sales decline last quarter?", "stream": False},
        headers={**_auth(token), "Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert env["route"] == "hybrid"
    answer = env["answer"].lower()
    assert "fell" in answer or "decline" in answer
    assert "north" in answer
    assert env["sql"], "structured path should have executed SQL"
    assert env["citations"], "hybrid answer should cite documents"
    assert env["chart"] is not None


def test_ask_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ask",
        json={"question": "revenue last quarter?", "stream": False},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_ask_sse_stream_has_ordered_events(client: TestClient) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask",
        json={"question": "Why did sales decline last quarter?", "stream": True},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert body.index("event: meta") < body.index("event: token")
    assert "event: sql" in body
    assert "event: citations" in body
    assert "event: done" in body
    assert body.index("event: token") < body.index("event: done")


def test_metrics_query_returns_rows(client: TestClient) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/metrics/query",
        json={
            "metric": "revenue",
            "dimensions": ["region"],
            "time_range": {"start": "2026-04-01", "end": "2026-06-30"},
            "order": "desc",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["row_count"] > 0
    assert result["rows"]
    assert result["sql"].lower().startswith("select")
    names = [c["name"] for c in result["columns"]]
    assert "region" in names and "revenue" in names


def test_viewer_denied_metrics_query(client: TestClient) -> None:
    token = _login(client, "viewer@insightgpt.dev", "viewer-pass")
    resp = client.post(
        "/api/v1/metrics/query",
        json={"metric": "revenue"},
        headers=_auth(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_viewer_denied_admin_pipeline_run(client: TestClient) -> None:
    token = _login(client, "viewer@insightgpt.dev", "viewer-pass")
    resp = client.post("/api/v1/pipelines/retail_elt/run", headers=_auth(token))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_metrics_catalog_visible_to_viewer(client: TestClient) -> None:
    token = _login(client, "viewer@insightgpt.dev", "viewer-pass")
    resp = client.get("/api/v1/metrics", headers=_auth(token))
    assert resp.status_code == 200
    keys = [m["key"] for m in resp.json()["metrics"]]
    assert "revenue" in keys


def test_refresh_mints_new_access_token(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@insightgpt.dev", "password": "admin-pass"},
    )
    refresh_token = resp.json()["refresh_token"]
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r2.status_code == 200
    assert r2.json()["access_token"]
