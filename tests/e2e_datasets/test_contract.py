"""The frozen API contract (§4), exercised through the real app.

Offline: the deterministic ``fake`` provider, the DuckDB fixture warehouse, and
a **recording stand-in for the indexing seam** so no Qdrant or Ollama is needed.
Parsing and redaction are NOT stubbed — they are the steps contract §6.4 makes a
promise about, so they run for real and the recorder captures exactly what text
would have been embedded.

Nothing here is marked xfail. A failure means the contract is not met.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="needs the services/api environment")
# `app.api.main` reaches sqlglot through the SQL guardrails, so guarding on
# fastapi alone still fails collection in the thin root environment - the
# import is transitive and does not announce itself.
pytest.importorskip("sqlglot", reason="needs the services/api environment")

from app.api import datasets_store as store  # noqa: E402
from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from .helpers import (  # noqa: E402
    ANALYST,
    API,
    SECRET_BODIES,
    SECRET_FILE,
    SECRET_FILE_SURVIVING_TEXT,
    VIEWER,
    auth,
    fixture_bytes,
    login,
    poll_status,
    upload,
)

DATASET_FIELDS = {
    "id", "name", "kind", "status", "bytes", "chunks", "redactions", "created_at", "error",
}
JOB_FIELDS = {"dataset_id", "status", "progress", "message"}


class IndexRecorder:
    """Stands in for the Qdrant write and remembers every call.

    Returns a chunk count so the dataset reaches ``ready`` exactly as it would
    with a real store, while the captured ``text`` lets the redaction promise be
    asserted on the bytes that WOULD have been embedded.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def index(self, **kwargs: Any) -> int:
        self.calls.append(kwargs)
        return max(1, len(kwargs.get("text", "")) // 400 + 1)

    def delete(self, dataset_id: str) -> int:
        self.deleted.append(dataset_id)
        return 1

    def text_for(self, name: str) -> str:
        for call in self.calls:
            if call.get("name") == name:
                return call.get("text", "")
        raise AssertionError(f"the indexer was never called for {name!r}; calls={self.calls}")


@pytest.fixture(scope="module")
def recorder() -> IndexRecorder:
    return IndexRecorder()


@pytest.fixture(scope="module")
def client(
    recorder: IndexRecorder, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    uploads = tmp_path_factory.mktemp("datasets")
    with pytest.MonkeyPatch.context() as mp:
        # The deployment an upload is MEANT for: only the Qdrant retriever can
        # read back an uploaded file. Set here (not in conftest) so these tests
        # describe a deployment where accepting a file is honest, and so they
        # keep describing it if the upload route starts declining when the
        # fixture retriever is selected.
        mp.setenv("RETRIEVER", "qdrant")
        mp.setattr(store, "index_dataset_text", lambda **kw: recorder.index(**kw))
        mp.setattr(store, "delete_dataset_points", recorder.delete)
        store.set_data_dir(uploads)
        store.reset_state()
        reset_caches()
        with TestClient(create_app()) as test_client:
            yield test_client
        store.reset_state()
        store.set_data_dir(None)


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> str:
    return login(client, *ANALYST)


@pytest.fixture(scope="module")
def viewer(client: TestClient) -> str:
    return login(client, *VIEWER)


# --- §4: the endpoints exist and answer in the frozen shapes ----------------


def test_the_five_routes_are_mounted(client: TestClient) -> None:
    paths = client.get(f"{API}/openapi.json").json()["paths"]
    assert "/api/v1/datasets" in paths
    assert {"post", "get"} <= set(paths["/api/v1/datasets"])
    assert {"get", "delete"} <= set(paths["/api/v1/datasets/{dataset_id}"])
    assert "get" in paths["/api/v1/datasets/{dataset_id}/status"]


def test_upload_returns_202_and_a_dataset_job(client: TestClient, analyst: str) -> None:
    response = upload(client, analyst, [("notes.md", b"# A heading\n\nSome prose.\n")])
    assert response.status_code == 202, response.text
    job = response.json()
    assert set(job) == JOB_FIELDS, job
    assert job["dataset_id"].startswith("ds_")
    assert len(job["dataset_id"]) == len("ds_") + 12
    assert job["status"] in ("pending", "indexing", "ready")
    assert 0.0 <= job["progress"] <= 1.0


def test_dataset_reaches_ready_and_matches_the_frozen_shape(
    client: TestClient, analyst: str
) -> None:
    job = upload(client, analyst, [("ready.md", b"# Title\n\nBody text here.\n")]).json()
    final = poll_status(client, analyst, job["dataset_id"], timeout=30)
    assert final["status"] == "ready", final

    body = client.get(f"{API}/datasets/{job['dataset_id']}", headers=auth(analyst)).json()
    assert set(body) == DATASET_FIELDS, body
    assert body["name"] == "ready.md"
    assert body["kind"] == "document"
    assert body["bytes"] > 0
    assert body["chunks"] >= 1, "a ready dataset with zero chunks indexed nothing"
    assert body["error"] is None
    # A server path is not the client's business, and the response model is what
    # keeps it off the wire.
    assert "path" not in body


def test_csv_is_tabular_and_markdown_is_document(client: TestClient, analyst: str) -> None:
    job = upload(client, analyst, [("rows.csv", b"a,b\n1,2\n3,4\n")], content_type="text/csv")
    dataset_id = job.json()["dataset_id"]
    poll_status(client, analyst, dataset_id, timeout=30)
    body = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
    assert body["kind"] == "tabular"


def test_list_contains_what_was_uploaded(client: TestClient, analyst: str) -> None:
    job = upload(client, analyst, [("listed.txt", b"listed content")]).json()
    listing = client.get(f"{API}/datasets", headers=auth(analyst))
    assert listing.status_code == 200
    ids = [d["id"] for d in listing.json()]
    assert job["dataset_id"] in ids
    for dataset in listing.json():
        assert set(dataset) == DATASET_FIELDS


def test_unknown_id_is_404_on_every_read(client: TestClient, analyst: str) -> None:
    for path in ("/datasets/ds_doesnotexist", "/datasets/ds_doesnotexist/status"):
        response = client.get(f"{API}{path}", headers=auth(analyst))
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == "not_found"


def test_delete_removes_the_record_and_the_points(client: TestClient, analyst: str) -> None:
    job = upload(client, analyst, [("doomed.md", b"# Doomed\n\ncontent\n")]).json()
    dataset_id = job["dataset_id"]
    poll_status(client, analyst, dataset_id, timeout=30)

    response = client.delete(f"{API}/datasets/{dataset_id}", headers=auth(analyst))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True}
    assert client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).status_code == 404


def test_deleting_an_indexed_dataset_is_a_filtered_delete(
    client: TestClient, analyst: str, recorder: IndexRecorder
) -> None:
    """§3/§4: removal addresses the dataset's points, not the collection."""
    job = upload(client, analyst, [("filtered.md", b"# F\n\nbody\n")]).json()
    dataset_id = job["dataset_id"]
    poll_status(client, analyst, dataset_id, timeout=30)
    client.delete(f"{API}/datasets/{dataset_id}", headers=auth(analyst))
    assert dataset_id in recorder.deleted, (
        "delete did not go through the dataset_id-filtered point delete"
    )


# --- §4: accepted types, rejections, caps -----------------------------------


@pytest.mark.parametrize(
    "filename",
    ["a.csv", "a.tsv", "a.txt", "a.md", "a.json"],
)
def test_accepted_text_types_are_accepted(
    client: TestClient, analyst: str, filename: str
) -> None:
    response = upload(client, analyst, [(filename, b"col\nvalue\n")])
    assert response.status_code == 202, (filename, response.text)


@pytest.mark.parametrize("filename", ["malware.exe", "archive.zip", "notes", "photo.png"])
def test_unsupported_types_are_400_naming_what_is_accepted(
    client: TestClient, analyst: str, filename: str
) -> None:
    response = upload(client, analyst, [(filename, b"whatever")])
    assert response.status_code == 400, (filename, response.text)
    error = response.json()["error"]
    accepted = error["details"]["accepted"]
    assert {".csv", ".tsv", ".xlsx", ".pdf", ".txt", ".md", ".json", ".docx"} == set(accepted)
    # The message itself has to say it, not only the details blob: the UI shows
    # the message.
    assert ".csv" in error["message"]


def test_oversized_file_is_413_not_a_hang(client: TestClient, analyst: str) -> None:
    oversized = b"x" * (store.MAX_FILE_BYTES + 1024)
    response = upload(client, analyst, [("huge.txt", oversized)])
    assert response.status_code == 413, response.text
    assert response.json()["error"]["details"]["max_file_bytes"] == store.MAX_FILE_BYTES


def test_more_than_ten_files_is_rejected(client: TestClient, analyst: str) -> None:
    files = [(f"f{i}.txt", b"content") for i in range(11)]
    response = upload(client, analyst, files)
    assert response.status_code == 413, response.text


# --- §4: role gates, enforced at the fetch ----------------------------------


def test_viewer_can_list_and_read(client: TestClient, analyst: str, viewer: str) -> None:
    job = upload(client, analyst, [("viewable.txt", b"content")]).json()
    assert client.get(f"{API}/datasets", headers=auth(viewer)).status_code == 200
    assert (
        client.get(f"{API}/datasets/{job['dataset_id']}", headers=auth(viewer)).status_code == 200
    )


def test_viewer_cannot_upload_or_delete(client: TestClient, analyst: str, viewer: str) -> None:
    job = upload(client, analyst, [("protected.txt", b"content")]).json()

    denied_upload = upload(client, viewer, [("sneaky.txt", b"content")])
    assert denied_upload.status_code == 403, denied_upload.text
    assert denied_upload.json()["error"]["code"] == "forbidden"

    denied_delete = client.delete(
        f"{API}/datasets/{job['dataset_id']}", headers=auth(viewer)
    )
    assert denied_delete.status_code == 403, denied_delete.text
    # The gate is at the fetch: the record must still be there.
    assert (
        client.get(f"{API}/datasets/{job['dataset_id']}", headers=auth(analyst)).status_code == 200
    )


def test_anonymous_access_is_401_everywhere(client: TestClient) -> None:
    assert client.get(f"{API}/datasets").status_code == 401
    assert client.post(f"{API}/datasets", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code == 401
    assert client.delete(f"{API}/datasets/ds_whatever").status_code == 401


# --- §6.4: redaction runs BEFORE embedding, always --------------------------


def test_secrets_are_redacted_before_the_indexer_sees_the_text(
    client: TestClient, analyst: str, recorder: IndexRecorder
) -> None:
    """The strongest offline form of §6.4: the text handed to the indexing seam
    is checked, so there is no path by which an unredacted string could reach a
    vector store even in principle."""
    job = upload(client, analyst, [(SECRET_FILE, fixture_bytes(SECRET_FILE))]).json()
    dataset_id = job["dataset_id"]
    final = poll_status(client, analyst, dataset_id, timeout=30)
    assert final["status"] == "ready", final

    embedded = recorder.text_for(SECRET_FILE)
    for secret in SECRET_BODIES:
        assert secret not in embedded, f"{secret!r} reached the indexer unredacted"
    # Redaction must not be implemented by throwing the document away.
    assert SECRET_FILE_SURVIVING_TEXT in embedded

    body = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
    assert body["redactions"] >= 3, (
        f"reported {body['redactions']} redactions for a file carrying "
        f"{len(SECRET_BODIES)} secret-shaped values"
    )
