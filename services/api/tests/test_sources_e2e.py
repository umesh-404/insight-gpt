"""Data sources end to end (§3.3) — seeding, registration, testing, deletion.

Everything here runs against a synthetic "deployment layout" in ``tmp_path``:
``GENERATED_DIR``/``DOCUMENT_CORPUS_PATH``/``POSTGRES_DSN`` are pointed at files
the test itself created, so the assertions are exact rather than dependent on
whether someone has run the generator on this machine. One generated extract
(``inventory.csv``) is deliberately left missing, because the interesting
behaviour is that a seed with no file behind it is reported as an error instead
of being quietly presented as healthy.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.routers import sources as src  # noqa: E402

# A credential that must never appear in a response body, a listing or a probe
# message, no matter which endpoint is asked.
SECRET = "sup3r-s3cret"
SEED_DSN = f"postgresql://reporter:{SECRET}@127.0.0.1:1/insight"

PRESENT_TABLES = ("customers", "products", "stores", "orders", "order_items")


@pytest.fixture(scope="module")
def deployment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    root = tmp_path_factory.mktemp("deployment")
    generated = root / "generated"
    generated.mkdir()
    for table in PRESENT_TABLES:
        (generated / f"{table}.csv").write_text("id\n1\n", encoding="utf-8")
    corpus = root / "ingested" / "documents.json"
    corpus.parent.mkdir()
    corpus.write_text("[]", encoding="utf-8")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GENERATED_DIR", str(generated))
        mp.setenv("DOCUMENT_CORPUS_PATH", str(corpus))
        mp.setenv("POSTGRES_DSN", SEED_DSN)
        src.reset_state()
        yield {"root": str(root), "generated": str(generated), "corpus": str(corpus)}
    src.reset_state()


@pytest.fixture(scope="module")
def client(deployment: dict[str, str]) -> TestClient:
    reset_caches()
    return TestClient(create_app())


@pytest.fixture(scope="module")
def admin(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@insightgpt.dev", "password": "admin-pass"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _list(client: TestClient, admin: dict[str, str]) -> list[dict]:
    resp = client.get("/api/v1/sources", headers=admin)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- seeding ------------------------------------------------------------------


def test_seeded_sources_describe_the_real_deployment(
    client: TestClient, admin: dict[str, str], deployment: dict[str, str]
) -> None:
    by_id = {s["id"]: s for s in _list(client, admin)}

    # Every generated extract is registered, present or not.
    for table in PRESENT_TABLES:
        row = by_id[f"src_seed_csv_{table}"]
        assert row["kind"] == "csv"
        assert row["name"] == f"{table}.csv"
        assert row["status"] == "untested"
        assert row["location"].endswith(f"{table}.csv")
        assert row["last_tested_at"] is None

    # The one nobody generated is an honest error, naming the missing path.
    missing = by_id["src_seed_csv_inventory"]
    assert missing["status"] == "error"
    assert "inventory.csv" in missing["detail"]

    corpus = by_id["src_seed_documents"]
    assert corpus["kind"] == "documents"
    assert corpus["location"] == deployment["corpus"]
    assert corpus["status"] == "untested"

    warehouse = by_id["src_seed_warehouse"]
    assert warehouse["kind"] == "postgres"
    assert warehouse["status"] == "untested"
    # Host and port only — no user, no password, no database name.
    assert warehouse["location"] == "127.0.0.1:1"


def test_seed_listing_never_leaks_the_configured_dsn(
    client: TestClient, admin: dict[str, str]
) -> None:
    resp = client.get("/api/v1/sources", headers=admin)
    assert SECRET not in resp.text
    assert "reporter" not in resp.text
    assert "dsn" not in resp.text


# --- registration --------------------------------------------------------------


def test_create_with_a_valid_path_succeeds_and_shows_up(
    client: TestClient, admin: dict[str, str], deployment: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/sources",
        json={
            "name": "extra extracts",
            "kind": "csv",
            "options": {"path": deployment["generated"], "timeout_s": 3},
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["status"] == "untested"
    assert created["location"] == deployment["generated"]
    assert "dsn" not in created and "options" not in created

    assert any(s["id"] == created["id"] for s in _list(client, admin))


def test_create_with_a_dsn_stores_it_write_only(
    client: TestClient, admin: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/sources",
        json={
            "name": "reporting replica",
            "kind": "postgres",
            "dsn": f"postgresql://reporter:{SECRET}@127.0.0.1:1/insight",
            "options": {"timeout_s": 2},
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    assert SECRET not in resp.text
    created = resp.json()
    assert created["location"] == "127.0.0.1:1"

    # And it stays invisible on every subsequent read of that source.
    for path in ("", f"/{created['id']}"):
        follow_up = client.get(f"/api/v1/sources{path}", headers=admin)
        assert SECRET not in follow_up.text


@pytest.mark.parametrize(
    ("body", "detail_key"),
    [
        ({"name": "no path", "kind": "csv"}, "expected_option"),
        ({"name": "blank path", "kind": "excel", "options": {"path": "  "}},
         "expected_option"),
        ({"name": "no dsn", "kind": "postgres"}, "expected_field"),
        ({"name": "blank dsn", "kind": "mysql", "dsn": "   "}, "expected_field"),
    ],
)
def test_missing_path_or_dsn_is_a_clean_400(
    client: TestClient, admin: dict[str, str], body: dict, detail_key: str
) -> None:
    resp = client.post("/api/v1/sources", json=body, headers=admin)
    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["code"] == "bad_request"
    assert error["message"]
    assert error["details"][detail_key] in {"path", "dsn"}
    # A rejected registration leaves nothing behind.
    assert not any(s["name"] == body["name"] for s in _list(client, admin))


# --- testing -------------------------------------------------------------------


def test_testing_a_seeded_source_updates_its_status_and_timestamp(
    client: TestClient, admin: dict[str, str]
) -> None:
    before = {s["id"]: s for s in _list(client, admin)}["src_seed_documents"]
    assert before["status"] == "untested" and before["last_tested_at"] is None

    resp = client.post("/api/v1/sources/src_seed_documents/test", headers=admin)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["ok"] is True
    assert result["checked"] == "filesystem"
    assert result["error_code"] is None

    after = {s["id"]: s for s in _list(client, admin)}["src_seed_documents"]
    assert after["status"] == "ok"
    assert after["last_tested_at"] is not None
    assert after["detail"] == result["message"]


def test_testing_a_missing_seed_reports_the_failure(
    client: TestClient, admin: dict[str, str]
) -> None:
    result = client.post(
        "/api/v1/sources/src_seed_csv_inventory/test", headers=admin
    ).json()
    assert result["ok"] is False
    assert result["error_code"] == "not_found"

    after = {s["id"]: s for s in _list(client, admin)}["src_seed_csv_inventory"]
    assert after["status"] == "error"
    assert after["last_tested_at"] is not None


def test_testing_the_seeded_warehouse_never_echoes_the_credential(
    client: TestClient, admin: dict[str, str]
) -> None:
    resp = client.post("/api/v1/sources/src_seed_warehouse/test", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is False  # port 1 is not a database
    assert SECRET not in resp.text
    assert "reporter" not in resp.text


# --- deletion ------------------------------------------------------------------


def test_delete_removes_a_seeded_source_and_it_does_not_come_back(
    client: TestClient, admin: dict[str, str]
) -> None:
    target = "src_seed_csv_stores"
    assert any(s["id"] == target for s in _list(client, admin))

    resp = client.delete(f"/api/v1/sources/{target}", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "deleted", "id": target}

    # Gone from the listing, and re-reading does not re-seed it.
    for _ in range(2):
        assert not any(s["id"] == target for s in _list(client, admin))
    assert client.get(f"/api/v1/sources/{target}", headers=admin).status_code == 404


def test_delete_removes_a_hand_registered_source(
    client: TestClient, admin: dict[str, str], deployment: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/sources",
        json={
            "name": "temporary",
            "kind": "csv",
            "options": {"path": deployment["generated"]},
        },
        headers=admin,
    ).json()
    assert client.delete(f"/api/v1/sources/{created['id']}", headers=admin).status_code == 200
    assert not any(s["id"] == created["id"] for s in _list(client, admin))


def test_the_listing_is_admin_only(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@insightgpt.dev", "password": "viewer-pass"},
    )
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    assert client.get("/api/v1/sources", headers=headers).status_code == 403
