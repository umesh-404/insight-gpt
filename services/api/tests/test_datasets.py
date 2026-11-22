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
