"""Uploaded datasets (contract §4) — offline, no Qdrant and no Ollama.

The indexing seam into the retrieval service is replaced with a recorder, which
is also how the ordering requirement is proved: the test asserts that the text
the indexer receives has already had its secrets removed, rather than trusting
that the call happens in the right order.

``BackgroundTasks`` runs after the response is produced but before
``TestClient`` returns, so a completed job is observable immediately and these
tests need no polling loop.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-32-bytes")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import io  # noqa: E402
import json  # noqa: E402
import zipfile  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import datasets_store as store  # noqa: E402
from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402

CSV_BODY = (
    "order_id,customer,email,total\n"
    "1001,Asha,asha.roy@example.com,2499.00\n"
    "1002,Vikram,vikram.s@example.com,899.50\n"
)

SECRET_DOC = (
    "# Deployment runbook\n\n"
    "The warehouse loader authenticates with a service token.\n"
    "github_token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'\n"
    "Escalate to ops@example.com if the load stalls.\n"
)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("ingested")
    store.set_data_dir(path)
    yield path
    store.set_data_dir(None)


@pytest.fixture(scope="module", autouse=True)
def searchable_deployment() -> Iterator[None]:
    """Configure these tests as a deployment that can read uploads back.

    The suite's conftest pins ``RETRIEVER=fixture`` so nothing reaches a live
    Qdrant, and the upload route refuses outright under that setting - an
    upload the retriever can never consult would be indexed, badged ready and
    then ignored by every question. That refusal is correct and is tested
    separately below; it just makes "fixture" the wrong backdrop for testing
    the upload contract itself. The indexing seam is still stubbed, so this
    opens no socket.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv("RETRIEVER", "qdrant")
    yield
    patch.undo()


@pytest.fixture(scope="module")
def client(data_dir: Path) -> TestClient:
    reset_caches()
    store.reset_state()
    return TestClient(create_app())


def _token(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> dict[str, str]:
    return _token(client, "analyst@insightgpt.dev", "analyst-pass")


@pytest.fixture(scope="module")
def viewer(client: TestClient) -> dict[str, str]:
    return _token(client, "viewer@insightgpt.dev", "viewer-pass")


class Recorder:
    """Stand-in for the retrieval service's ``index_dataset``."""

    def __init__(self, chunks: int = 3) -> None:
        self.chunks = chunks
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> int:
        self.calls.append(kwargs)
        return self.chunks


@pytest.fixture
def indexer(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(store, "index_dataset_text", recorder)
    return recorder


def _upload(client: TestClient, headers: dict[str, str], *files, field: str = "files"):
    payload = [(field, f) for f in files]
    return client.post("/api/v1/datasets", headers=headers, files=payload)


def test_both_spellings_of_the_repeated_field_are_accepted(
    client: TestClient, analyst: dict[str, str], indexer: Recorder
) -> None:
    resp = _upload(
        client, analyst, ("notes.md", b"# Notes\n", "text/markdown"), field="files[]"
    )
    assert resp.status_code == 202, resp.text
    assert len(indexer.calls) == 1


def test_an_upload_with_no_file_is_a_400(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.post("/api/v1/datasets", headers=analyst, data={"title": "x"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["details"]["retryable"] is False


# --------------------------------------------------------------------------
# Validation: specific, early, and permanent where it is permanent
# --------------------------------------------------------------------------
def test_contract_limits_are_the_documented_ones() -> None:
    assert store.MAX_FILE_BYTES == 25 * 1024 * 1024
    assert store.MAX_REQUEST_BYTES == 50 * 1024 * 1024
    assert store.MAX_FILES == 10
    assert sorted(store.ACCEPTED) == [
        ".csv", ".docx", ".json", ".md", ".pdf", ".tsv", ".txt", ".xlsx",
    ]


def test_unsupported_type_is_a_permanent_400_naming_what_is_accepted(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = _upload(client, analyst, ("payload.exe", b"MZ\x90\x00", "application/octet-stream"))
    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["code"] == "unsupported_file_type"
    assert ".csv" in error["message"] and ".pdf" in error["message"]
    assert error["details"]["retryable"] is False
    assert ".xlsx" in error["details"]["accepted"]


def test_oversized_file_is_413_not_a_hang(
    client: TestClient, analyst: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_FILE_BYTES", 2048)
    resp = _upload(client, analyst, ("big.csv", b"x" * 9000, "text/csv"))
    assert resp.status_code == 413, resp.text
    error = resp.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["details"]["max_file_bytes"] == 2048


def test_request_budget_is_enforced_across_files(
    client: TestClient, analyst: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_REQUEST_BYTES", 4096)
    resp = _upload(
        client,
        analyst,
        ("a.csv", b"a" * 3000, "text/csv"),
        ("b.csv", b"b" * 3000, "text/csv"),
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["details"]["max_request_bytes"] == 4096
    # The first file was already written when the second blew the budget; a
    # rejected batch leaves nothing registered as "pending" forever.
    assert all(d.name != "a.csv" for d in store.list_all())


def test_too_many_files_is_rejected(client: TestClient, analyst: dict[str, str]) -> None:
    many = [(f"f{i}.csv", b"a,b\n1,2\n", "text/csv") for i in range(11)]
    resp = _upload(client, analyst, *many)
    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["details"]["max_files"] == 10


def test_a_bad_file_rejects_the_whole_batch_before_anything_is_written(
    client: TestClient, analyst: dict[str, str], data_dir: Path, indexer: Recorder
) -> None:
    before = {p.name for p in data_dir.iterdir()}
    resp = _upload(
        client,
        analyst,
        ("good.csv", CSV_BODY.encode(), "text/csv"),
        ("bad.exe", b"MZ", "application/octet-stream"),
    )
    assert resp.status_code == 400, resp.text
    assert {p.name for p in data_dir.iterdir()} == before
    assert indexer.calls == []


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------
def test_upload_persists_redacts_indexes_and_reports(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str],
    data_dir: Path, indexer: Recorder,
) -> None:
    resp = _upload(client, analyst, ("runbook.md", SECRET_DOC.encode(), "text/markdown"))
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert set(job) == {"dataset_id", "status", "progress", "message"}
    dataset_id = job["dataset_id"]
    assert dataset_id.startswith("ds_")

    # The raw file is kept so a re-index needs no re-upload.
    stored = data_dir / dataset_id / "runbook.md"
    assert stored.is_file()
    assert stored.read_bytes() == SECRET_DOC.encode()

    # Status is pollable and reaches a terminal state.
    poll = client.get(f"/api/v1/datasets/{dataset_id}/status", headers=viewer)
    assert poll.status_code == 200, poll.text
    assert poll.json()["status"] == "ready"
    assert poll.json()["progress"] == 1.0

    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    assert set(read) == {
        "id", "name", "kind", "status", "bytes", "chunks",
        "redactions", "created_at", "error",
    }
    assert read["name"] == "runbook.md"
    assert read["kind"] == "document"
    assert read["status"] == "ready"
    assert read["bytes"] == len(SECRET_DOC.encode())
    assert read["chunks"] == 3
    assert read["redactions"] >= 2  # the token and the email address
    assert read["error"] is None
    # A server path is not part of the wire shape.
    assert "path" not in read and "progress" not in read


def test_redaction_runs_before_anything_is_handed_to_the_indexer(
    client: TestClient, analyst: dict[str, str], indexer: Recorder
) -> None:
    resp = _upload(client, analyst, ("secrets.md", SECRET_DOC.encode(), "text/markdown"))
    assert resp.status_code == 202, resp.text
    assert len(indexer.calls) == 1
    handed_over = indexer.calls[0]["text"]
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in handed_over
    assert "ops@example.com" not in handed_over
    assert "REDACTED" in handed_over
    # The dataset_id travels with the text: every point must carry it, because
    # deletion is a filtered delete on that field (contract §4).
    assert indexer.calls[0]["dataset_id"] == resp.json()["dataset_id"]


def test_csv_is_tabular_and_its_contact_columns_are_redacted(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str], indexer: Recorder
) -> None:
    resp = _upload(client, analyst, ("orders.csv", CSV_BODY.encode(), "text/csv"))
    dataset_id = resp.json()["dataset_id"]
    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    assert read["kind"] == "tabular"
    assert read["redactions"] >= 2
    assert "asha.roy@example.com" not in indexer.calls[0]["text"]


def test_json_without_records_is_reported_as_a_document(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str], indexer: Recorder
) -> None:
    body = json.dumps({"settings": {"region": "south", "threshold": 3}}).encode()
    resp = _upload(client, analyst, ("config.json", body, "application/json"))
    dataset_id = resp.json()["dataset_id"]
    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    # Extension says tabular; the parse found no table, so the record says so.
    assert read["kind"] == "document"


def test_binary_office_formats_are_parsed_not_rejected(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str], indexer: Recorder
) -> None:
    """A .docx arrives as bytes, so the base64 hand-off has to survive it."""
    buffer = io.BytesIO()
    document = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Quarterly inventory notes</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    resp = _upload(
        client, analyst,
        ("notes.docx", buffer.getvalue(),
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )
    dataset_id = resp.json()["dataset_id"]
    assert client.get(
        f"/api/v1/datasets/{dataset_id}", headers=viewer
    ).json()["status"] == "ready"
    assert "Quarterly inventory notes" in indexer.calls[0]["text"]


def test_multiple_files_become_one_dataset_each(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str], indexer: Recorder
) -> None:
    store.reset_state()
    resp = _upload(
        client,
        analyst,
        ("a.csv", CSV_BODY.encode(), "text/csv"),
        ("b.md", b"# Notes\n\nNothing secret here.\n", "text/markdown"),
    )
    assert resp.status_code == 202, resp.text
    listed = client.get("/api/v1/datasets", headers=viewer).json()
    assert len(listed) == 2
    assert {d["name"] for d in listed} == {"a.csv", "b.md"}
    assert all(d["status"] == "ready" for d in listed)
    # The job names every id it queued so a client can poll each one.
    for dataset in listed:
        assert dataset["id"] in resp.json()["message"]


def test_a_traversing_filename_stays_inside_its_own_folder(
    client: TestClient, analyst: dict[str, str], data_dir: Path, indexer: Recorder
) -> None:
    resp = _upload(client, analyst, ("../../.env", b"notes\n", "text/plain"))
    # No extension on the multipart name, so it is refused outright.
    assert resp.status_code == 400
    resp = _upload(client, analyst, ("../../evil.md", b"notes\n", "text/markdown"))
    dataset_id = resp.json()["dataset_id"]
    written = list((data_dir / dataset_id).iterdir())
    assert [p.name for p in written] == ["evil.md"]
    assert not (data_dir.parent / "evil.md").exists()


# --------------------------------------------------------------------------
# Failure is a recorded state, never a dataset stuck at "indexing"
# --------------------------------------------------------------------------
def test_indexing_failure_records_the_reason(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**kwargs):
        raise store.IndexingUnavailableError("Qdrant is unreachable")

    monkeypatch.setattr(store, "index_dataset_text", boom)
    resp = _upload(client, analyst, ("notes.md", b"# Notes\n", "text/markdown"))
    assert resp.status_code == 202, resp.text
    dataset_id = resp.json()["dataset_id"]
    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    assert read["status"] == "failed"
    assert "Qdrant is unreachable" in read["error"]
    assert read["chunks"] == 0
    job = client.get(f"/api/v1/datasets/{dataset_id}/status", headers=viewer).json()
    assert job["status"] == "failed"


def test_the_real_seam_always_reaches_a_terminal_state_with_a_reason(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str]
) -> None:
    """No recorder here: the worker calls the real indexing seam.

    Whether the retrieval package happens to be installed in this environment,
    and whether Qdrant happens to be reachable, the dataset must end up at a
    terminal status — and a failure must carry its reason. A dataset left at
    "pending" or "indexing" with nothing coming for it is the state contract
    §6.6 forbids.
    """
    resp = _upload(client, analyst, ("plain.txt", b"hello\n", "text/plain"))
    dataset_id = resp.json()["dataset_id"]
    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    assert read["status"] in {"ready", "failed"}
    if read["status"] == "failed":
        assert read["error"]
        assert read["chunks"] == 0
    job = client.get(f"/api/v1/datasets/{dataset_id}/status", headers=viewer).json()
    assert job["progress"] == 1.0


def test_zero_chunks_is_not_reported_as_ready(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that yielded no indexable text has not been indexed, and a green
    "ready" badge over an empty index would be the lie contract §6.3 forbids."""
    monkeypatch.setattr(store, "index_dataset_text", lambda **_: 0)
    resp = _upload(client, analyst, ("scanned.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))
    dataset_id = resp.json()["dataset_id"]
    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    assert read["status"] == "failed"
    assert read["chunks"] == 0
    # A scanned PDF with no text layer is now caught at the parse step rather
    # than after a zero-chunk index, so the reason names the actual problem
    # instead of its symptom. Assert the diagnosis is present and specific,
    # not one exact sentence - the wording belongs to the parser.
    assert read["error"]
    assert "pdf" in read["error"].lower()


def test_an_unparseable_file_is_never_reported_ready(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str],
) -> None:
    """The parser reports failure as PROSE, which is the trap.

    ``process_attachment`` never raises: for the /ask path a sentence saying a
    spreadsheet could not be read is a reasonable thing to show beside an
    answer. On this path that sentence was redacted, chunked, embedded into
    the vector store and reported ``ready`` with a real-looking chunk count -
    a searchable document whose entire content is an apology, wearing a green
    badge. Nothing is stubbed here: the bytes really are not a spreadsheet.
    """
    resp = _upload(
        client,
        analyst,
        (
            "broken.xlsx",
            b"this is not a zip file at all",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    )
    dataset_id = resp.json()["dataset_id"]
    read = client.get(f"/api/v1/datasets/{dataset_id}", headers=viewer).json()
    assert read["status"] == "failed", "a file that could not be read is not ready"
    assert read["chunks"] == 0, "nothing of it should have reached the index"
    assert read["error"]


def test_unknown_dataset_is_a_404(client: TestClient, viewer: dict[str, str]) -> None:
    for path in ("/api/v1/datasets/ds_missing", "/api/v1/datasets/ds_missing/status"):
        resp = client.get(path, headers=viewer)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------
# Roles, gated at the fetch
# --------------------------------------------------------------------------
def test_viewer_may_read_but_not_upload_or_delete(
    client: TestClient, viewer: dict[str, str], analyst: dict[str, str], indexer: Recorder
) -> None:
    assert client.get("/api/v1/datasets", headers=viewer).status_code == 200

    denied = _upload(client, viewer, ("notes.md", b"# Notes\n", "text/markdown"))
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["details"]["required"] == "analyst"

    created = _upload(client, analyst, ("notes.md", b"# Notes\n", "text/markdown"))
    dataset_id = created.json()["dataset_id"]
    assert client.delete(
        f"/api/v1/datasets/{dataset_id}", headers=viewer
    ).status_code == 403


def test_every_dataset_route_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/datasets").status_code == 401
    assert client.get("/api/v1/datasets/ds_x").status_code == 401
    assert client.get("/api/v1/datasets/ds_x/status").status_code == 401
    assert client.delete("/api/v1/datasets/ds_x").status_code == 401
    unauthenticated = client.post(
        "/api/v1/datasets", files=[("files", ("a.csv", b"a,b\n", "text/csv"))]
    )
    assert unauthenticated.status_code == 401


# --------------------------------------------------------------------------
# Deletion
# --------------------------------------------------------------------------
def test_delete_removes_points_by_filter_files_and_record(
    client: TestClient, analyst: dict[str, str], data_dir: Path,
    indexer: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(
        store, "delete_dataset_points", lambda dataset_id: deleted.append(dataset_id) or 3
    )
    resp = _upload(client, analyst, ("orders.csv", CSV_BODY.encode(), "text/csv"))
    dataset_id = resp.json()["dataset_id"]
    assert (data_dir / dataset_id).is_dir()

    gone = client.delete(f"/api/v1/datasets/{dataset_id}", headers=analyst)
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"deleted": True}
    assert deleted == [dataset_id]
    assert not (data_dir / dataset_id).exists()
    assert client.get(f"/api/v1/datasets/{dataset_id}", headers=analyst).status_code == 404


def test_delete_that_cannot_remove_points_does_not_claim_success(
    client: TestClient, analyst: dict[str, str], indexer: Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _upload(client, analyst, ("orders.csv", CSV_BODY.encode(), "text/csv"))
    dataset_id = resp.json()["dataset_id"]

    def boom(_id: str) -> int:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(store, "delete_dataset_points", boom)
    failed = client.delete(f"/api/v1/datasets/{dataset_id}", headers=analyst)
    assert failed.status_code == 503, failed.text
    assert failed.json()["error"]["code"] == "dependency_unavailable"
    # The record survives, so the orphaned points stay discoverable.
    assert client.get(f"/api/v1/datasets/{dataset_id}", headers=analyst).status_code == 200


def test_delete_of_a_never_indexed_dataset_needs_no_vector_call(
    client: TestClient, analyst: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_id: str) -> int:  # pragma: no cover - must not be reached
        raise AssertionError("no points were written, so none may be deleted")

    monkeypatch.setattr(store, "delete_dataset_points", boom)
    monkeypatch.setattr(store, "index_dataset_text", lambda **_: 0)
    resp = _upload(client, analyst, ("plain.txt", b"hello\n", "text/plain"))
    dataset_id = resp.json()["dataset_id"]
    gone = client.delete(f"/api/v1/datasets/{dataset_id}", headers=analyst)
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"deleted": True}


# --------------------------------------------------------------------------
# Contract §3: the shared Qdrant instance
# --------------------------------------------------------------------------
def test_a_foreign_collection_name_is_refused_in_code() -> None:
    """The instance also holds another project's ``code``/``docs``/``memory``.
    The prefix check is an assertion in the delete path, not a convention."""
    for foreign in ("code", "docs", "memory", "documents"):
        with pytest.raises(RuntimeError, match="Refusing to modify"):
            store._assert_ours(foreign)
    store._assert_ours("insightgpt_documents")  # ours: allowed


def test_the_store_exposes_no_collection_level_operation() -> None:
    source = Path(store.__file__).read_text(encoding="utf-8")
    for forbidden in ("delete_collection", "recreate_collection", "drop_collection"):
        assert forbidden not in source


def test_an_upload_is_refused_when_it_could_never_be_searched(
    data_dir: Path, analyst: dict[str, str]
) -> None:
    """Refuse the file rather than index it into a void.

    With the fixture retriever configured, an uploaded file would be parsed,
    redacted, embedded, recorded `ready` with a real chunk count - and then
    never consulted by a single question, because the retriever answering
    them cannot read that index. The badge would be green and the feature
    would be inert. Saying no is the only honest answer.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv("RETRIEVER", "fixture")
    try:
        reset_caches()
        client = TestClient(create_app())
        resp = _upload(client, analyst, ("notes.txt", b"hello world", "text/plain"))
        assert resp.status_code == 503, resp.text
        body = resp.json()["error"]
        assert body["code"] == "dependency_unavailable"
        assert "RETRIEVER" in body["message"]
        # Permanent for this deployment: a retry changes nothing until the
        # configuration does, so the client must not offer one.
        assert body["details"]["retryable"] is False
    finally:
        patch.undo()
        reset_caches()


# --------------------------------------------------------------------------
# The registry survives a restart (phase 2)
#
# It used to be a process-local dict, which is the worst of the available
# options: restart the API and the UI's dataset list is empty while the raw
# files and the Qdrant points are both still there, so the product denies the
# existence of data it is still holding. A "restart" below is a second
# `DatasetRegistry` over the same file, which is what a new process does.
# --------------------------------------------------------------------------
def _record(dataset_id: str = "ds_aaaaaaaaaaaa", **over) -> store.StoredDataset:
    fields: dict = {
        "id": dataset_id,
        "name": "orders.csv",
        "kind": "tabular",
        "status": "ready",
        "bytes": 1024,
        "chunks": 3,
        "redactions": 2,
        "created_at": datetime(2026, 11, 22, 10, 0, tzinfo=UTC),
        "progress": 1.0,
        "message": "Indexed 3 chunk(s)",
        "path": "/tmp/orders.csv",
    }
    fields.update(over)
    return store.StoredDataset(**fields)


@pytest.fixture
def restore_registry() -> Iterator[None]:
    """Put the module-wide registry back, whatever a test did to it."""
    previous = store.registry()
    try:
        yield
    finally:
        store.set_registry(previous)


def test_the_dataset_list_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "datasets.json"
    first = store.DatasetRegistry(path)
    assert first.durable is True
    assert first.backend == "file"
    first.put(_record("ds_111111111111", name="a.csv"))
    first.put(_record("ds_222222222222", name="b.md", kind="document"))

    restarted = store.DatasetRegistry(path)
    assert {d.id for d in restarted.list_all()} == {"ds_111111111111", "ds_222222222222"}
    recovered = restarted.get("ds_111111111111")
    assert recovered is not None
    assert recovered.name == "a.csv"
    assert recovered.chunks == 3
    # The server-side path survives too, so a re-index needs no re-upload.
    assert recovered.path == "/tmp/orders.csv"


def test_a_memory_registry_does_not_claim_to_be_durable() -> None:
    memory = store.DatasetRegistry()
    assert memory.durable is False
    assert memory.backend == "memory"


def test_a_deletion_is_persisted_too(tmp_path: Path) -> None:
    path = tmp_path / "datasets.json"
    registry = store.DatasetRegistry(path)
    registry.put(_record("ds_333333333333"))
    registry.remove("ds_333333333333")
    assert store.DatasetRegistry(path).list_all() == []


def test_the_write_is_atomic_and_leaves_nothing_half_written(tmp_path: Path) -> None:
    path = tmp_path / "datasets.json"
    registry = store.DatasetRegistry(path)
    registry.put(_record())
    # tmp-then-rename: the temporary file is moved onto the target, never left.
    assert [p.name for p in tmp_path.iterdir()] == ["datasets.json"]
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)


def test_a_corrupt_registry_file_is_not_fatal(tmp_path: Path) -> None:
    """A registry that cannot be read is an empty list and a warning, not a
    crash loop: the API must still start, and the next write repairs the file."""
    path = tmp_path / "datasets.json"
    path.write_text("{ this is not the registry", encoding="utf-8")
    registry = store.DatasetRegistry(path)
    assert registry.list_all() == []
    registry.put(_record("ds_444444444444"))
    assert store.DatasetRegistry(path).get("ds_444444444444") is not None


def test_progress_is_not_persisted_but_everything_else_is(tmp_path: Path) -> None:
    """Progress describes a job in flight and no job survives a restart, so it
    is not worth a disk write per embedding batch. Status is."""
    path = tmp_path / "datasets.json"
    registry = store.DatasetRegistry(path)
    registry.put(_record("ds_555555555555", status="indexing", progress=0.0, chunks=0))

    registry.update("ds_555555555555", progress=0.7, message="embedded 64 of 90 chunks")
    on_disk = store.DatasetRegistry(path).get("ds_555555555555")
    assert on_disk is not None
    assert on_disk.progress == 0.0, "an ephemeral tick must not cost a disk write"

    registry.update("ds_555555555555", status="ready", chunks=9, progress=1.0)
    reloaded = store.DatasetRegistry(path).get("ds_555555555555")
    assert reloaded is not None
    assert (reloaded.status, reloaded.chunks) == ("ready", 9)


def test_an_upload_is_still_listed_after_a_restart(
    tmp_path: Path, analyst: dict[str, str], viewer: dict[str, str],
    indexer: Recorder, restore_registry: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point, end to end: upload, throw the process away, list."""
    path = tmp_path / "datasets.json"
    store.set_registry(store.DatasetRegistry(path))
    reset_caches()
    client = TestClient(create_app())
    resp = _upload(client, analyst, ("orders.csv", CSV_BODY.encode(), "text/csv"))
    assert resp.status_code == 202, resp.text
    dataset_id = resp.json()["dataset_id"]

    # Restart: a new registry over the same file, and a vector store that
    # still holds the points (the real situation - dropping the in-process
    # registry never deleted anything else).
    monkeypatch.setattr(store, "_qdrant_chunk_count", lambda _id: 3)
    store.set_registry(store.DatasetRegistry(path))
    reset_caches()
    restarted = TestClient(create_app())

    listed = restarted.get("/api/v1/datasets", headers=viewer).json()
    assert [d["id"] for d in listed] == [dataset_id]
    assert listed[0]["status"] == "ready"
    assert listed[0]["chunks"] == 3
    assert restarted.get(
        f"/api/v1/datasets/{dataset_id}", headers=viewer
    ).status_code == 200


# --------------------------------------------------------------------------
# Reconciliation: the file and the vector store can disagree
#
# Contract 3.6 - a dataset is never `ready` over an empty index and never left
# at `indexing` with nothing coming for it. Both are reachable purely by
# restarting at the wrong moment, so both are resolved on load.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "points", "expected_status", "expected_chunks"),
    [
        # Recorded as indexed and the points are there: believed, and the count
        # is taken from Qdrant rather than from what the last process thought
        # it wrote.
        ("ready", 7, "ready", 7),
        # Recorded as indexed and the points are gone: the badge was a lie.
        ("ready", 0, "failed", 0),
        # Killed mid-job but the write had landed: only bookkeeping was lost.
        ("indexing", 5, "ready", 5),
        ("pending", 5, "ready", 5),
        # Killed mid-job with nothing written: nothing is coming to finish it.
        ("indexing", 0, "failed", 0),
        ("pending", 0, "failed", 0),
        # Already failed: the failure stands. `chunks` is still corrected, so
        # DELETE removes the orphaned points instead of skipping the vector
        # cleanup and leaving them searchable for ever.
        ("failed", 4, "failed", 4),
        ("failed", 0, "failed", 0),
    ],
)
def test_reconciliation_rules(
    status: str, points: int, expected_status: str, expected_chunks: int
) -> None:
    registry = store.DatasetRegistry()
    registry.put(_record("ds_666666666666", status=status, chunks=3))
    changed = registry.reconcile(lambda _id: points)

    record = registry.get("ds_666666666666")
    assert record is not None
    assert record.status == expected_status
    assert record.chunks == expected_chunks
    if expected_status == "failed" and status != "failed":
        assert record.error, "a dataset demoted on boot has to say why"
        assert record.progress == 1.0, "and it is not still in flight"
    if record.status != status:
        assert "ds_666666666666" in changed


def test_reconciliation_leaves_a_ready_dataset_alone_when_qdrant_cannot_be_asked() -> None:
    """An unreachable vector store is not evidence that the points are gone.

    Treating "could not ask" as zero would mark every dataset failed the first
    time the API booted before Qdrant did - a different lie in the same place.
    """

    def unreachable(_id: str) -> int:
        raise RuntimeError("connection refused")

    registry = store.DatasetRegistry()
    registry.put(_record("ds_777777777777", status="ready", chunks=3))
    registry.put(_record("ds_888888888888", status="indexing", chunks=0, progress=0.6))
    registry.reconcile(unreachable)

    still_ready = registry.get("ds_777777777777")
    assert still_ready is not None
    assert still_ready.status == "ready"
    assert still_ready.chunks == 3

    # The interrupted one is resolved regardless: no worker survived the
    # restart either way, so sitting at "indexing" for ever is not an option.
    interrupted = registry.get("ds_888888888888")
    assert interrupted is not None
    assert interrupted.status == "failed"
    # ...and the reason says the index could not be checked, rather than
    # claiming nothing was indexed, which nobody here actually knows.
    assert "could not be reached" in interrupted.error
    assert "nothing of this file reached the index" not in interrupted.error


def test_reconciliation_is_idempotent_and_runs_once_per_registry() -> None:
    asked: list[str] = []

    def counter(dataset_id: str) -> int:
        asked.append(dataset_id)
        return 0

    registry = store.DatasetRegistry()
    registry.put(_record("ds_999999999999", status="ready"))
    assert registry.reconcile(counter) == ["ds_999999999999"]
    # Second pass: already failed, nothing left to change.
    assert registry.reconcile(counter) == []
    registry.reconcile_once(counter)
    assert len(asked) == 2, "reconcile_once must not re-ask after a pass"


def test_the_reconciled_failure_explains_itself_in_the_api(
    tmp_path: Path, viewer: dict[str, str], restore_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the user sees after the points went away: a failure with a reason,
    not a green badge over an empty index."""
    path = tmp_path / "datasets.json"
    seeded = store.DatasetRegistry(path)
    seeded.put(_record("ds_aaa000000000", status="ready", chunks=3))

    monkeypatch.setattr(store, "_qdrant_chunk_count", lambda _id: 0)
    store.set_registry(store.DatasetRegistry(path))
    reset_caches()
    client = TestClient(create_app())

    read = client.get("/api/v1/datasets/ds_aaa000000000", headers=viewer).json()
    assert read["status"] == "failed"
    assert read["chunks"] == 0
    assert "no chunks" in read["error"].lower()


# --------------------------------------------------------------------------
# Progress: real work, or no motion at all
# --------------------------------------------------------------------------
def test_the_indexer_is_given_a_progress_callback_and_the_bar_moves(
    client: TestClient, analyst: dict[str, str], viewer: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embed is the slow step, and it used to be a single 0.45 -> 1.0 jump.

    The stub plays the part of the indexer reporting per embedding batch. What
    is asserted is the plumbing: the callback arrives, what it reports reaches
    the poll endpoint, and the bar only ever moves forward.
    """
    seen: list[float] = []

    def indexer(*, progress=None, **kwargs) -> int:
        assert callable(progress), "the indexer must be handed a progress callback"
        # The indexer's own scale: it starts near zero ("reading the file"),
        # after this worker has already parsed and redacted.
        for fraction, message in (
            (0.05, "reading the file"),
            (0.2, "extracted 4,096 characters"),
            (0.3, "embedding"),
            (0.44, "embedded 32 of 96 chunks"),
            (0.58, "embedded 64 of 96 chunks"),
            (0.85, "embedded 96 of 96 chunks"),
            (0.9, "written to the vector store"),
        ):
            progress(fraction, message)
            record = store.get(kwargs["dataset_id"])
            assert record is not None
            seen.append(record.progress)
        return 4

    monkeypatch.setattr(store, "index_dataset_text", indexer)
    resp = _upload(client, analyst, ("big.md", b"# Notes\n\nlots of text\n", "text/markdown"))
    dataset_id = resp.json()["dataset_id"]

    assert seen == sorted(seen), "a progress bar must never run backwards"
    # Three distinct values during the embed alone, where there used to be one
    # jump. The granularity is per batch, so it tracks work, not a timer.
    during_embed = sorted({round(p, 3) for p in seen[3:6]})
    assert len(during_embed) == 3
    assert all(0.45 < p < 1.0 for p in during_embed)

    final = client.get(f"/api/v1/datasets/{dataset_id}/status", headers=viewer).json()
    assert final["status"] == "ready"
    assert final["progress"] == 1.0
    assert final["message"] == "Indexed 4 chunk(s)"


def test_a_progress_report_for_a_deleted_dataset_is_harmless(
    client: TestClient, analyst: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dataset deleted mid-index must not be resurrected by its own worker."""
    from app.api.routers import datasets as router

    report = router._progress_reporter("ds_not_here")
    report(0.5, "embedding")  # must not raise, must not create a record
    assert store.get("ds_not_here") is None
