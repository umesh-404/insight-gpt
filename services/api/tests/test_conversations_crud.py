"""Rename + delete for conversations (PATCH/DELETE ``/conversations/{id}``).

The history sidebar is only usable if a thread can be named and removed. These
tests pin the contract, and — more importantly — the security property the read
path already had: a conversation belonging to another user is reported as
*missing*, never *forbidden*, so ids cannot be probed across accounts.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

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


def _start(client: TestClient, token: str, question: str = QUESTION) -> str:
    """Create one conversation via /ask and return its id.

    Looked up by title rather than by list position: two conversations created
    in the same tick can tie on ``updated_at``, and the ordering of a tie is not
    part of the contract.
    """
    _ask(client, token, question)
    page = client.get("/api/v1/conversations", headers=_auth(token)).json()
    matches = [c for c in page["items"] if c["title"] == store.derive_title(question)]
    assert matches, page
    return str(matches[0]["id"])


# --- store unit behaviour ---------------------------------------------------


def test_normalize_title_collapses_whitespace() -> None:
    assert store.normalize_title("  Q3   revenue \n dip ") == "Q3 revenue dip"
    assert store.normalize_title("   ") == ""
    assert store.normalize_title("\t\n") == ""


def test_rename_and_delete_are_scoped_to_the_owner() -> None:
    store.append_turn(
        user_id="u1", conversation_id="c_1", question="q", message_id="m_1",
        envelope=AnswerEnvelope(answer="a"),
    )
    assert store.rename_conversation("u2", "c_1", "Stolen") is None
    assert store.delete_conversation("u2", "c_1") is False
    assert store.get_conversation("u1", "c_1") is not None

    summary = store.rename_conversation("u1", "c_1", "Mine")
    assert summary is not None and summary.title == "Mine"
    assert store.delete_conversation("u1", "c_1") is True
    assert store.get_conversation("u1", "c_1") is None


# --- rename -----------------------------------------------------------------


def test_rename_updates_title_and_persists(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    cid = _start(client, token)

    resp = client.patch(
        f"/api/v1/conversations/{cid}",
        json={"title": "  Q3   revenue dip  "},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == cid
    assert body["title"] == "Q3 revenue dip", "whitespace is collapsed"
    assert body["message_count"] == 2

    listed = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert listed["items"][0]["title"] == "Q3 revenue dip"
    full = client.get(f"/api/v1/conversations/{cid}", headers=_auth(token)).json()
    assert full["title"] == "Q3 revenue dip"


def test_renamed_title_survives_a_later_turn(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    cid = _start(client, token)
    client.patch(f"/api/v1/conversations/{cid}", json={"title": "Named"}, headers=_auth(token))

    _ask(client, token, "And what about inventory?", cid)

    listed = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["title"] == "Named"
    assert listed["items"][0]["message_count"] == 4


@pytest.mark.parametrize("title", ["", " ", "   \t  ", "\n"])
def test_blank_titles_are_rejected(client: TestClient, title: str) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    cid = _start(client, token)
    resp = client.patch(
        f"/api/v1/conversations/{cid}", json={"title": title}, headers=_auth(token)
    )
    # "" fails the pydantic min_length (422); whitespace-only survives that and
    # is caught by the normalizer (400). Both are clean, typed envelopes.
    assert resp.status_code in (400, 422), resp.text
    assert client.get(
        f"/api/v1/conversations/{cid}", headers=_auth(token)
    ).json()["title"] == QUESTION


def test_overly_long_title_is_rejected(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    cid = _start(client, token)
    too_long = "x" * (store.TITLE_INPUT_MAX_CHARS + 1)
    resp = client.patch(
        f"/api/v1/conversations/{cid}", json={"title": too_long}, headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text

    at_limit = "y" * store.TITLE_INPUT_MAX_CHARS
    ok = client.patch(
        f"/api/v1/conversations/{cid}", json={"title": at_limit}, headers=_auth(token)
    )
    assert ok.status_code == 200
    assert ok.json()["title"] == at_limit


def test_rename_of_unknown_conversation_is_404(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    resp = client.patch(
        "/api/v1/conversations/c_missing", json={"title": "Nope"}, headers=_auth(token)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_rename_across_users_is_404_not_403(client: TestClient) -> None:
    analyst = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    admin = _token(client, "admin@insightgpt.dev", "admin-pass")
    cid = _start(client, analyst)

    resp = client.patch(
        f"/api/v1/conversations/{cid}", json={"title": "Hijacked"}, headers=_auth(admin)
    )
    assert resp.status_code == 404, "another user's id must look missing, not forbidden"
    assert resp.json()["error"]["code"] == "not_found"

    mine = client.get(f"/api/v1/conversations/{cid}", headers=_auth(analyst)).json()
    assert mine["title"] == QUESTION


# --- delete -----------------------------------------------------------------


def test_delete_removes_from_list_and_detail(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    cid = _start(client, token)

    resp = client.delete(f"/api/v1/conversations/{cid}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "deleted", "id": cid}

    listed = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert listed["total"] == 0 and listed["items"] == []
    assert client.get(f"/api/v1/conversations/{cid}", headers=_auth(token)).status_code == 404


def test_delete_leaves_other_conversations_alone(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    first = _start(client, token, "First question")
    second = _start(client, token, "Second question")
    assert first != second

    assert client.delete(f"/api/v1/conversations/{first}", headers=_auth(token)).status_code == 200
    listed = client.get("/api/v1/conversations", headers=_auth(token)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == second


def test_delete_is_not_idempotent_second_time_is_404(client: TestClient) -> None:
    token = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    cid = _start(client, token)
    assert client.delete(f"/api/v1/conversations/{cid}", headers=_auth(token)).status_code == 200
    assert client.delete(f"/api/v1/conversations/{cid}", headers=_auth(token)).status_code == 404


def test_delete_across_users_is_404_not_403(client: TestClient) -> None:
    analyst = _token(client, "analyst@insightgpt.dev", "analyst-pass")
    admin = _token(client, "admin@insightgpt.dev", "admin-pass")
    cid = _start(client, analyst)

    resp = client.delete(f"/api/v1/conversations/{cid}", headers=_auth(admin))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    # The owner's transcript is untouched.
    assert client.get(f"/api/v1/conversations/{cid}", headers=_auth(analyst)).status_code == 200


def test_mutations_require_auth(client: TestClient) -> None:
    assert client.patch("/api/v1/conversations/c_x", json={"title": "t"}).status_code == 401
    assert client.delete("/api/v1/conversations/c_x").status_code == 401
