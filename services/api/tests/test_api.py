"""API surface tests — fully offline (fixture engine + fake provider).

Exercises the contract paths that matter: health, login -> ask (non-stream)
happy path, role gating, and the governed metric query.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_engine, reset_caches
from app.api.main import create_app
from app.engine.guardrails import GuardrailError


@pytest.fixture(scope="module")
def app():
    reset_caches()
    return create_app()


@pytest.fixture(scope="module")
def client(app) -> TestClient:
    return TestClient(app)


def _sse_events(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into ordered ``(event, data)`` pairs."""
    events: list[tuple[str, dict]] = []
    for record in body.split("\n\n"):
        record = record.strip()
        if not record:
            continue
        name, payload = "message", []
        for line in record.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                payload.append(line[5:].strip())
        if payload:
            events.append((name, json.loads("\n".join(payload))))
    return events


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


def test_ask_accepts_multipart_attachment(client: TestClient) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask",
        data={"question": "Review this CSV and tell me the strongest trend.", "stream": "false"},
        files={"files": ("sales.csv", "region,revenue\nNorth,120\nSouth,90\n", "text/csv")},
        headers={**_auth(token), "Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert env["answer"]
    assert env["route"] in {"structured", "hybrid", "unstructured"}


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


def test_sse_stream_emits_every_table_in_the_envelope(client: TestClient) -> None:
    """The breakdown tables must survive streaming, not just tables[0]."""
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    question = "Why did sales decline last quarter?"

    envelope = client.post(
        "/api/v1/ask",
        json={"question": question, "stream": False},
        headers={**_auth(token), "Accept": "application/json"},
    ).json()
    assert len(envelope["tables"]) > 1, "fixture answer should carry breakdown tables"

    resp = client.post(
        "/api/v1/ask", json={"question": question, "stream": True}, headers=_auth(token)
    )
    streamed = [data for name, data in _sse_events(resp.text) if name == "tables"]
    assert [t["name"] for t in streamed] == [t["title"] for t in envelope["tables"]]
    assert [t["columns"] for t in streamed] == [t["columns"] for t in envelope["tables"]]
    assert [t["rows"] for t in streamed] == [t["rows"] for t in envelope["tables"]]


def test_assembled_stream_equals_the_json_envelope(client: TestClient) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    question = "Why did sales decline last quarter?"

    envelope = client.post(
        "/api/v1/ask",
        json={"question": question, "stream": False},
        headers={**_auth(token), "Accept": "application/json"},
    ).json()
    events = _sse_events(
        client.post(
            "/api/v1/ask", json={"question": question, "stream": True}, headers=_auth(token)
        ).text
    )

    answer = "".join(d["text"] for n, d in events if n == "token")
    assert answer == envelope["answer"]

    by_name = {n: d for n, d in events}
    assert by_name["sql"]["sql"].split(";\n\n") == envelope["sql"]
    assert by_name["citations"]["items"] == envelope["citations"]
    assert by_name["chart"]["chart_spec"] == envelope["chart"]
    assert by_name.get("caveats", {"items": []})["items"] == envelope["caveats"]
    assert by_name["route"]["route"] == envelope["route"]
    assert by_name["route"]["confidence"] == envelope["confidence"]


class _BoomEngine:
    """Stand-in engine whose ``ask`` always fails."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def ask(self, question: str):
        raise self._exc


@pytest.fixture()
def failing_engine(app) -> Iterator[list[Exception]]:
    """Override the engine dependency; the list holds the exception to raise."""
    box: list[Exception] = [RuntimeError("boom")]
    app.dependency_overrides[get_engine] = lambda: _BoomEngine(box[0])
    yield box
    app.dependency_overrides.pop(get_engine, None)


def test_engine_failure_returns_a_clean_error_envelope(
    client: TestClient, failing_engine: list[Exception]
) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask",
        json={"question": "revenue last quarter?", "stream": False},
        headers={**_auth(token), "Accept": "application/json"},
    )
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "internal_error"
    assert "boom" not in error["message"] and "Traceback" not in error["message"]
    assert error["request_id"]


def test_guardrail_rejection_is_a_400_not_a_500(
    client: TestClient, failing_engine: list[Exception]
) -> None:
    failing_engine[0] = GuardrailError("query references non-allow-listed table(s): ['secrets']")
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask",
        json={"question": "select everything", "stream": False},
        headers={**_auth(token), "Accept": "application/json"},
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "bad_request"
    assert "secrets" not in error["message"]


def test_stream_failure_emits_a_terminal_error_event(
    client: TestClient, failing_engine: list[Exception]
) -> None:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask", json={"question": "anything", "stream": True}, headers=_auth(token)
    )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    names = [n for n, _ in events]
    assert names == ["meta", "error"]
    error = events[-1][1]
    assert error["code"] == "internal_error"
    assert "boom" not in error["message"]
    assert error["request_id"] != "-"


def test_stream_guardrail_failure_is_labelled(
    client: TestClient, failing_engine: list[Exception]
) -> None:
    failing_engine[0] = GuardrailError("nope")
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask", json={"question": "anything", "stream": True}, headers=_auth(token)
    )
    events = _sse_events(resp.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "guardrail_rejected"
