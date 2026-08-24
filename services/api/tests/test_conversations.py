"""Conversation persistence: /ask writes history, /conversations reads it back.

Covers the two things that were broken — turns were never stored, and the SSE
stream dropped every table after the first — plus per-user isolation and the
store's own bounds.
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

from app.api import conversations as store
from app.api.deps import reset_caches
from app.api.main import create_app
from app.engine.envelope import AnswerEnvelope

QUESTION = "Why did sales decline last quarter?"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    reset_caches()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_store() -> Iterator[None]:
    store.reset()
    yield
    store.reset()


def _token(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _ask(client: TestClient, token: str, question: str, conversation_id: str | None = None):
    resp = client.post(
        "/api/v1/ask",
        json={"question": question, "conversation_id": conversation_id, "stream": False},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sse_events(body: str) -> list[tuple[str, dict]]:
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


# --- store unit behaviour ---------------------------------------------------


def test_title_is_derived_and_truncated() -> None:
    assert store.derive_title("  short   question  ") == "short question"
    long = "x" * 200
    title = store.derive_title(long)
    assert len(title) == store.TITLE_MAX_CHARS
    assert title.endswith("…")


def test_store_is_bounded_and_evicts_oldest() -> None:
    for i in range(store.MAX_CONVERSATIONS + 5):
        store.append_turn(
            user_id="u1",
            conversation_id=f"c_{i}",
            question=f"q{i}",
            message_id=f"m_{i}",
            envelope=AnswerEnvelope(answer="a"),
        )
    page = store.list_conversations("u1", limit=100)
    assert page.total == store.MAX_CONVERSATIONS
    assert store.get_conversation("u1", "c_0") is None
    assert store.get_conversation("u1", f"c_{store.MAX_CONVERSATIONS + 4}") is not None


def test_reads_are_snapshots_not_live_state() -> None:
    store.append_turn(
        user_id="u1", conversation_id="c_x", question="q", message_id="m_1",
        envelope=AnswerEnvelope(answer="a"),
    )
    view = store.get_conversation("u1", "c_x")
    assert view is not None
    view.messages.clear()
    assert len(store.get_conversation("u1", "c_x").messages) == 2


# --- persistence through /ask ----------------------------------------------


def test_conversation_persists_across_two_ask_calls(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")

    listed = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert listed["total"] == 0

    _ask(client, token, QUESTION)
    page = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert page["total"] == 1
    summary = page["items"][0]
    assert summary["title"] == QUESTION
    assert summary["message_count"] == 2
    conversation_id = summary["id"]

    _ask(client, token, "And what about inventory?", conversation_id)
    page = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert page["total"] == 1, "the second turn must join the same conversation"
    assert page["items"][0]["message_count"] == 4
    # Title stays anchored on the first question.
    assert page["items"][0]["title"] == QUESTION

    full = client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth(token)).json()
    roles = [m["role"] for m in full["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert full["messages"][0]["content"] == QUESTION
    assert full["messages"][2]["content"] == "And what about inventory?"
    # Assistant messages carry the full envelope so the turn re-renders offline.
    envelope = full["messages"][1]["envelope"]
    assert envelope["answer"] == full["messages"][1]["content"]
    assert envelope["sql"] and envelope["citations"]
    assert full["messages"][0]["envelope"] is None
    assert full["updated_at"] >= full["created_at"]


def test_streaming_ask_also_persists(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.post(
        "/api/v1/ask",
        json={"question": QUESTION, "stream": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    events = dict(_sse_events(resp.text))
    conversation_id = events["meta"]["conversation_id"]

    full = client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth(token)).json()
    assert len(full["messages"]) == 2
    assert full["messages"][1]["id"] == events["meta"]["message_id"]


def test_conversations_are_isolated_per_user(client: TestClient) -> None:
    analyst = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    admin = _token(client, "admin@insightgpt.dev", "admin-pass")

    _ask(client, analyst, QUESTION)
    conversation_id = client.get(
        "/api/v1/conversations", headers=_auth(analyst)
    ).json()["items"][0]["id"]

    assert client.get("/api/v1/conversations", headers=_auth(admin)).json()["total"] == 0
    other = client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth(admin))
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "not_found"

    # A collision on someone else's id starts a *new* conversation, it never joins.
    _ask(client, admin, "Admin question", conversation_id)
    mine = client.get(f"/api/v1/conversations/{conversation_id}", headers=_auth(analyst)).json()
    assert len(mine["messages"]) == 2
    assert mine["messages"][0]["content"] == QUESTION


def test_conversation_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/conversations").status_code == 401
    assert client.get("/api/v1/conversations/c_nope").status_code == 401


def test_unknown_conversation_is_404(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.get("/api/v1/conversations/c_missing", headers=_auth(token))
    assert resp.status_code == 404


def test_pagination_window(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    for _ in range(3):
        _ask(client, token, QUESTION)
    page = client.get("/api/v1/conversations?limit=2&offset=0", headers=_auth(token)).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["limit"] == 2 and page["offset"] == 0
    tail = client.get("/api/v1/conversations?limit=2&offset=2", headers=_auth(token)).json()
    assert len(tail["items"]) == 1


def test_bad_pagination_is_rejected(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    assert client.get("/api/v1/conversations?limit=0", headers=_auth(token)).status_code == 422
