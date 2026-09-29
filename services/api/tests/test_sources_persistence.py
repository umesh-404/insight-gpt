"""The source registry must survive a restart — without persisting secrets.

A "restart" here is `_set_store` pointing the router at a fresh `SourceStore`
over the same file: the in-process caches are dropped and everything must be
rebuilt from disk, which is exactly what a new uvicorn process does.

The security property under test is as important as the durability one: a
connection string is a live credential, so it must never appear in the file the
registry is written to, and a source that had one must say so rather than let a
later test fail mysteriously.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.api.routers import sources as sources_router  # noqa: E402
from app.api.sources_store import SourceStore, sanitize_options  # noqa: E402

ADMIN = {"email": "admin@insightgpt.dev", "password": "admin-pass"}
SECRET_DSN = "postgresql://someone:hunter2-do-not-persist@db.internal:5432/warehouse"


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A file-backed registry, plus a way to simulate a process restart."""
    store_file = tmp_path / "sources.json"
    # Keep seeding deterministic and off this machine's real data directory.
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("DOCUMENT_CORPUS_PATH", str(tmp_path / "corpus.json"))
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    def restart() -> None:
        """Drop every in-process cache and rebuild from the file, as a boot does."""
        sources_router._set_store(SourceStore(file_path=store_file))

    restart()
    yield store_file, restart
    sources_router._set_store(SourceStore())
    sources_router.reset_state()


def _client() -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app)
    token = client.post("/api/v1/auth/login", json=ADMIN).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _names(client: TestClient, headers: dict[str, str]) -> list[str]:
    res = client.get("/api/v1/sources", headers=headers)
    assert res.status_code == 200, res.text
    return [s["name"] for s in res.json()]


# --------------------------------------------------------------------------- #
# Durability                                                                   #
# --------------------------------------------------------------------------- #
def test_a_registered_source_survives_a_restart(registry) -> None:
    store_file, restart = registry
    client, headers = _client()

    created = client.post(
        "/api/v1/sources", headers=headers,
        json={"name": "quarterly drop", "kind": "csv", "options": {"path": "/tmp/drop"}},
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    assert store_file.exists(), "registering a source must write the registry"

    restart()

    after = client.get(f"/api/v1/sources/{source_id}", headers=headers)
    assert after.status_code == 200
    assert after.json()["name"] == "quarterly drop"
    assert after.json()["location"] == "/tmp/drop"


def test_a_deleted_source_stays_deleted_after_a_restart(registry) -> None:
    """The regression that motivated persistence: deletions must not undo."""
    store_file, restart = registry
    client, headers = _client()

    before = _names(client, headers)
    assert before, "the deployment should seed some sources"
    victim = client.get("/api/v1/sources", headers=headers).json()[0]

    assert client.delete(f"/api/v1/sources/{victim['id']}", headers=headers).status_code == 200
    assert victim["name"] not in _names(client, headers)

    restart()

    assert victim["name"] not in _names(client, headers), "a deleted source came back"
    assert client.get(f"/api/v1/sources/{victim['id']}", headers=headers).status_code == 404


def test_a_test_result_survives_a_restart(registry, tmp_path: Path) -> None:
    store_file, restart = registry
    client, headers = _client()
    folder = tmp_path / "extracts"
    folder.mkdir()
    (folder / "a.csv").write_text("x\n", encoding="utf-8")

    created = client.post(
        "/api/v1/sources", headers=headers,
        json={"name": "extracts", "kind": "csv", "options": {"path": str(folder)}},
    ).json()
    probe = client.post(f"/api/v1/sources/{created['id']}/test", headers=headers).json()
    assert probe["ok"] is True

    restart()

    row = client.get(f"/api/v1/sources/{created['id']}", headers=headers).json()
    assert row["status"] == "ok"
    assert row["last_tested_at"], "the tested-at timestamp must survive"
    assert "readable" in (row["detail"] or "")


def test_seeding_only_happens_on_a_brand_new_registry(registry) -> None:
    """A stored registry is authoritative — seeds must not fight the operator."""
    store_file, restart = registry
    client, headers = _client()

    seeded = _names(client, headers)
    for name in list(seeded):
        target = next(s for s in client.get("/api/v1/sources", headers=headers).json()
                      if s["name"] == name)
        client.delete(f"/api/v1/sources/{target['id']}", headers=headers)
    assert _names(client, headers) == []

    restart()

    assert _names(client, headers) == [], "an emptied registry re-seeded itself"


# --------------------------------------------------------------------------- #
# Secrets                                                                      #
# --------------------------------------------------------------------------- #
def test_a_connection_string_is_never_written_to_the_registry(registry) -> None:
    store_file, _restart = registry
    client, headers = _client()

    created = client.post(
        "/api/v1/sources", headers=headers,
        json={"name": "prod warehouse", "kind": "postgres", "dsn": SECRET_DSN},
    )
    assert created.status_code == 201, created.text

    raw = store_file.read_text(encoding="utf-8")
    assert "hunter2-do-not-persist" not in raw, "the password reached the registry file"
    assert SECRET_DSN not in raw
    # The non-secret host:port is fine and is what makes the row meaningful.
    assert "db.internal:5432" in raw


def test_a_literal_dsn_is_gone_after_a_restart_and_the_row_says_so(registry) -> None:
    store_file, restart = registry
    client, headers = _client()
    created = client.post(
        "/api/v1/sources", headers=headers,
        json={"name": "prod warehouse", "kind": "postgres", "dsn": SECRET_DSN},
    ).json()

    restart()

    row = client.get(f"/api/v1/sources/{created['id']}", headers=headers).json()
    assert row["name"] == "prod warehouse", "the source itself must survive"
    assert "not persisted" in (row["detail"] or "").lower()

    # Testing it fails honestly rather than pretending or blowing up.
    probe = client.post(f"/api/v1/sources/{created['id']}/test", headers=headers)
    assert probe.status_code == 200
    assert probe.json()["ok"] is False
    assert probe.json()["error_code"] == "missing_dsn"


def test_a_dsn_env_source_survives_a_restart_intact(registry, monkeypatch) -> None:
    """Referencing a variable persists the *name*, so the source stays usable."""
    store_file, restart = registry
    monkeypatch.setenv("MY_WAREHOUSE_DSN", SECRET_DSN)
    client, headers = _client()

    created = client.post(
        "/api/v1/sources", headers=headers,
        json={
            "name": "warehouse by env", "kind": "postgres", "dsn": SECRET_DSN,
            "options": {"dsn_env": "MY_WAREHOUSE_DSN"},
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    raw = store_file.read_text(encoding="utf-8")
    assert "hunter2-do-not-persist" not in raw
    assert "MY_WAREHOUSE_DSN" in raw, "the variable name is what makes this work"

    restart()

    row = client.get(f"/api/v1/sources/{source_id}", headers=headers).json()
    assert "not persisted" not in (row["detail"] or "").lower()
    stored = sources_router._SOURCES[source_id]
    assert stored.dsn is not None, "the DSN should be re-resolved from the environment"
    assert stored.dsn.get_secret_value() == SECRET_DSN


def test_dsn_env_pointing_at_an_unset_variable_is_explained(registry, monkeypatch) -> None:
    store_file, restart = registry
    monkeypatch.setenv("TEMP_DSN", SECRET_DSN)
    client, headers = _client()
    created = client.post(
        "/api/v1/sources", headers=headers,
        json={"name": "gone", "kind": "postgres", "dsn": SECRET_DSN,
              "options": {"dsn_env": "TEMP_DSN"}},
    ).json()

    monkeypatch.delenv("TEMP_DSN", raising=False)
    restart()

    row = client.get(f"/api/v1/sources/{created['id']}", headers=headers).json()
    assert "TEMP_DSN" in (row["detail"] or "")


def test_sanitize_options_drops_credential_shaped_keys() -> None:
    cleaned = sanitize_options(
        {"path": "/data", "dsn": "postgresql://u:p@h/db", "password": "x",
         "API_KEY": "y", "timeout_s": 5},
    )
    assert cleaned == {"path": "/data", "timeout_s": 5}


# --------------------------------------------------------------------------- #
# Store behaviour                                                              #
# --------------------------------------------------------------------------- #
def test_store_reports_whether_it_is_actually_durable(tmp_path: Path) -> None:
    assert SourceStore().durable is False, "memory-only must not claim durability"
    assert SourceStore(file_path=tmp_path / "s.json").durable is True


def test_a_corrupt_registry_file_does_not_take_the_api_down(tmp_path: Path) -> None:
    corrupt = tmp_path / "sources.json"
    corrupt.write_text("{not json at all", encoding="utf-8")
    store = SourceStore(file_path=corrupt)
    assert store.all() == [], "a corrupt file should read as empty, not raise"


def test_the_registry_file_is_written_atomically(tmp_path: Path) -> None:
    """A crash mid-write must not leave a truncated registry behind."""
    path = tmp_path / "nested" / "sources.json"
    store = SourceStore(file_path=path)
    store.upsert_many([
        sources_router.SourceRecord(id="a", name="A", kind="csv"),
        sources_router.SourceRecord(id="b", name="B", kind="csv"),
    ])
    assert json.loads(path.read_text(encoding="utf-8")), "registry should be valid JSON"
    assert not list(path.parent.glob("*.tmp")), "the temp file must be renamed away"


def test_the_default_registry_path_is_anchored_at_the_repo_root() -> None:
    """A relative default would follow the process's cwd, not the repo.

    The API is normally launched from ``services/api``, where a bare
    ``data/sources.json`` resolves to ``services/api/data/`` — still durable, but
    outside the directory anything ignores or looks in. This caught exactly that.
    """
    from app.api.sources_store import _default_store_path

    resolved = _default_store_path()
    assert resolved.name == "sources.json"
    assert resolved.parent.name == "data"
    # The marker the repo root is found by must sit beside that data directory.
    assert (resolved.parent.parent / "config" / "semantic_layer.yml").exists()
    assert "services" not in resolved.parts, "the registry escaped into a service dir"


def test_a_test_run_never_writes_to_the_real_registry(monkeypatch) -> None:
    """Under pytest the default is memory, so a suite cannot clobber a dev's data."""
    from app.api.sources_store import store_from_env

    monkeypatch.delenv("SOURCES_STORE_PATH", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    assert store_from_env().backend == "memory"

    # An explicit path still wins, which is how the durability tests above run.
    monkeypatch.setenv("SOURCES_STORE_PATH", "ignored.json")
    assert store_from_env().backend == "file"
