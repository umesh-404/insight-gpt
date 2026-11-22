"""Contract §6 — the honesty rules, probed rather than assumed.

These are the rules least likely to be caught by a normal feature test: every
one of them is a way the product can look like it works while telling the user
something untrue. Each test below names the rule it defends.

The retriever is a stub whose results the test controls, so "what is in the
index" is an input rather than an accident of what someone last ran. The
provider is the deterministic ``fake`` one, so an assertion about the answer
text is stable.
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
from app.api.deps import get_engine, reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.engine.engine import InsightEngine  # noqa: E402
from app.engine.retrieval import RetrievedDoc  # noqa: E402
from app.providers.factory import get_provider  # noqa: E402
from app.semantic.catalog import load_catalog  # noqa: E402
from app.warehouse.executor import DuckDBWarehouse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from .helpers import (  # noqa: E402
    ANALYST,
    API,
    LIGHTHOUSE_FILE,
    RIDERSHIP_FILE,
    VIEWER,
    absolute_numbers,
    ask,
    auth,
    citation_mentions,
    fixture_bytes,
    login,
    numbers_in_evidence,
    poll_status,
    upload,
)

RIDERSHIP_TEXT = fixture_bytes(RIDERSHIP_FILE).decode()
LIGHTHOUSE_TEXT = fixture_bytes(LIGHTHOUSE_FILE).decode()

#: A figure that exists only in the retail fixture warehouse. An answer that
#: quotes it in response to a question about an uploaded file has substituted
#: one dataset for another.
RETAIL_ONLY_MARKERS = ("48.9Cr", "489164981", "48.9 Cr")


class StubRetriever:
    """A retriever whose index contents the test sets directly."""

    def __init__(self) -> None:
        self.docs: list[RetrievedDoc] = []
        self.searches: list[tuple[str, dict | None]] = []

    def search(self, query: str, *, filters: dict | None = None, k: int = 5):
        self.searches.append((query, filters))
        return list(self.docs[:k])

    def set_dataset_docs(self) -> None:
        """An index holding exactly the two uploaded fixture datasets."""
        self.docs = [
            RetrievedDoc(
                doc_id="ds_abc123def456::0",
                source_type="dataset",
                title=RIDERSHIP_FILE,
                body=RIDERSHIP_TEXT,
                date="2026-11-22",
                score=0.91,
                metadata={"dataset_id": "ds_abc123def456", "dataset_name": RIDERSHIP_FILE},
            ),
            RetrievedDoc(
                doc_id="ds_feed00112233::0",
                source_type="dataset",
                title=LIGHTHOUSE_FILE,
                body=LIGHTHOUSE_TEXT,
                date="2026-11-22",
                score=0.84,
                metadata={"dataset_id": "ds_feed00112233", "dataset_name": LIGHTHOUSE_FILE},
            ),
        ]

    def set_empty(self) -> None:
        self.docs = []


@pytest.fixture(scope="module")
def retriever() -> StubRetriever:
    return StubRetriever()


def _engine(retriever: StubRetriever) -> InsightEngine:
    catalog = load_catalog()
    return InsightEngine(
        catalog=catalog,
        warehouse=DuckDBWarehouse(allow_tables=set(catalog.allow_tables)),
        retriever=retriever,
        provider=get_provider("fake"),
    )


@pytest.fixture(scope="module")
def client(
    retriever: StubRetriever, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    uploads = tmp_path_factory.mktemp("honesty")
    with pytest.MonkeyPatch.context() as mp:
        # The indexing seam is stubbed: these tests are about what the system
        # SAYS, and a live vector store would make the assertions depend on
        # whatever was last indexed on the machine.
        mp.setenv("RETRIEVER", "qdrant")  # see test_contract.py::client
        mp.setattr(store, "index_dataset_text", lambda **kw: 3)
        mp.setattr(store, "delete_dataset_points", lambda dataset_id: 3)
        store.set_data_dir(uploads)
        store.reset_state()
        reset_caches()
        app = create_app()
        app.dependency_overrides[get_engine] = lambda: _engine(retriever)
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()
        store.reset_state()
        store.set_data_dir(None)


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> str:
    return login(client, *ANALYST)


@pytest.fixture(scope="module")
def viewer(client: TestClient) -> str:
    return login(client, *VIEWER)


def _assert_no_retail_figures(envelope: dict[str, Any], *, because: str) -> None:
    answer = envelope["answer"]
    for marker in RETAIL_ONLY_MARKERS:
        assert marker not in answer, (
            f"{because}: the answer quotes {marker!r}, which exists only in the retail "
            f"fixture warehouse.\nanswer={answer!r}"
        )


# --- §6.1 — an uploaded CSV does NOT become a governed metric ---------------


def test_dataset_question_answers_from_the_dataset_not_the_warehouse(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    """§6.1, the retrieval-shaped phrasing.

    A question that reaches the document path must be answered from the
    documents, carry no governed SQL, and quote no figure that is not in the
    evidence it returned.
    """
    retriever.set_dataset_docs()
    envelope = ask(
        client, analyst, f"Summarize {RIDERSHIP_FILE}: what was ridership in March?"
    )

    _assert_no_retail_figures(envelope, because="a question about an uploaded CSV")
    assert envelope["sql"] == [], (
        "a question about an uploaded file ran governed SQL over the retail warehouse: "
        f"{envelope['sql']}"
    )
    assert envelope["tables"] == []
    fabricated = absolute_numbers(envelope["answer"]) - numbers_in_evidence(
        envelope, RIDERSHIP_TEXT, LIGHTHOUSE_TEXT
    )
    assert not fabricated, (
        f"the answer quotes {sorted(fabricated)}, which appears neither in the uploaded "
        f"file nor in any returned table or citation"
    )


def test_naming_an_uploaded_file_does_not_silently_return_retail_revenue(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    """§6.1, the dangerous phrasing — the single most important check here.

    "What was revenue in <my file>?" is the question a demo audience asks. The
    honest outcomes are: answer from the file via retrieval, or decline. What
    must never happen is a retail-fixture number presented as the answer to a
    question about the user's upload.
    """
    retriever.set_dataset_docs()
    envelope = ask(client, analyst, f"What was revenue in {RIDERSHIP_FILE}?")

    if envelope.get("abstained"):
        return  # declining is a correct outcome

    answer_names_the_file = RIDERSHIP_FILE.lower() in envelope["answer"].lower()
    cites_the_file = citation_mentions(envelope, RIDERSHIP_FILE)
    assert cites_the_file or not envelope["sql"], (
        "the question named an uploaded file and the answer came from governed SQL "
        "with no citation to that file"
    )
    _assert_no_retail_figures(
        envelope, because="a question naming an uploaded file"
    )
    assert answer_names_the_file or cites_the_file, (
        "the answer neither cites nor names the file the question asked about, so the "
        "reader cannot tell which dataset the figure describes"
    )


def test_a_governed_question_is_not_attributed_to_an_upload(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    """§6.1, the mirror image: a genuine governed question must not pick up the
    uploaded dataset as a citation and imply the number came from it."""
    retriever.set_dataset_docs()
    envelope = ask(client, analyst, "What was revenue last quarter?")
    assert envelope["sql"], "a governed metric question should query the warehouse"
    assert not citation_mentions(envelope, RIDERSHIP_FILE), (
        "a warehouse figure was presented with the uploaded CSV as its citation"
    )


def test_an_upload_is_refused_when_it_could_never_be_searched(
    client: TestClient, analyst: str
) -> None:
    """A file this deployment cannot read back must be declined, not accepted.

    Accepting it is the silent substitution of §6.1 wearing a progress bar: the
    row says ready, the chunk count says real content, and the answer comes from
    a corpus the user never uploaded.
    """
    from app.engine.build import datasets_are_searchable

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RETRIEVER", "fixture")
        assert not datasets_are_searchable(), "precondition: this deployment cannot search"
        response = upload(client, analyst, [("unsearchable.md", b"# Content\n\nbody\n")])

    assert response.status_code >= 400, (
        f"HTTP {response.status_code}: the upload was accepted by a deployment whose "
        "retriever cannot read it back"
    )


# --- §6.2 — say which dataset an answer came from ---------------------------


def test_an_answer_from_an_upload_names_that_dataset(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    retriever.set_dataset_docs()
    envelope = ask(client, analyst, f"Summarize {LIGHTHOUSE_FILE} for me")
    assert envelope["citations"], "an answer built from documents carried no citations"
    assert citation_mentions(envelope, LIGHTHOUSE_FILE), (
        "no citation identifies the uploaded dataset by name, so the reader cannot tell "
        f"which file the answer came from: {envelope['citations']}"
    )


# --- §6.3 — an empty index is an empty index --------------------------------


def test_nothing_uploaded_means_ask_does_not_look_like_it_searched(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    retriever.set_empty()
    for question in (
        "Summarize the uploaded documents",
        "What are customers complaining about this month?",
        f"What does {LIGHTHOUSE_FILE} say?",
    ):
        envelope = ask(client, analyst, question)
        assert envelope["citations"] == [], (
            f"{question!r} returned citations from an empty index: {envelope['citations']}"
        )
        assert "[1]" not in envelope["answer"], (
            f"{question!r} produced a citation marker with nothing to resolve it"
        )
        assert envelope["abstained"] or "couldn't" in envelope["answer"].lower(), (
            f"{question!r} did not say it found nothing: {envelope['answer']!r}"
        )
        _assert_no_retail_figures(envelope, because="an empty index")


def test_an_empty_index_is_not_filled_in_from_the_retail_corpus(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    """A placeholder result is the specific failure §6.3 forbids: the retail
    sample corpus must not stand in for an empty uploaded index."""
    retriever.set_empty()
    envelope = ask(client, analyst, "Summarize the uploaded documents")
    for citation in envelope["citations"]:
        assert not citation["doc_id"].startswith(("TICKET-", "REVIEW-", "REPORT-", "EMAIL-")), (
            f"a retail fixture document was returned as a result: {citation}"
        )


# --- §6.5 — role copy must be true ------------------------------------------


def test_a_viewer_is_never_told_it_can_upload(
    client: TestClient, analyst: str, viewer: str
) -> None:
    """The API side of §6.5: the gate is real, and the refusal does not invite a
    retry that cannot succeed (§6.7)."""
    response = upload(client, viewer, [("nope.txt", b"content")])
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "forbidden"
    message = error["message"].lower()
    for invitation in ("try again", "retry", "please try"):
        assert invitation not in message, (
            f"a 403 told the viewer to {invitation!r}; a role failure is permanent (§6.7)"
        )
    assert error.get("details", {}).get("retryable") is not True
    assert "Retry-After" not in response.headers


def test_the_role_gate_is_on_the_write_routes_only(client: TestClient, viewer: str) -> None:
    """The UI gates its upload control on the caller's role, so the API's own
    split has to match the copy: a viewer reads and is refused every write."""
    reads = (
        client.get(f"{API}/datasets", headers=auth(viewer)),
        client.get(f"{API}/datasets/ds_nothing", headers=auth(viewer)),
    )
    assert [r.status_code for r in reads] == [200, 404], [r.status_code for r in reads]

    writes = (
        upload(client, viewer, [("x.txt", b"x")]),
        client.delete(f"{API}/datasets/ds_nothing", headers=auth(viewer)),
    )
    for response in writes:
        assert response.status_code == 403, response.text
        # 403 before 404: a viewer must not learn which ids exist by probing
        # the route it is not allowed to use.
        assert response.json()["error"]["code"] == "forbidden"


# --- §6.6 — a failure is a failure ------------------------------------------


#: Files whose bytes cannot be read as the type their extension claims.
CORRUPT_FILES = {
    "xlsx": ("corrupt.xlsx", b"PK\x03\x04 this is not a workbook " + bytes(range(256)) * 4),
    "pdf": ("corrupt.pdf", b"%PDF-1.4 this is not a PDF body"),
    "docx": ("corrupt.docx", b"PK\x03\x04 this is not a Word document"),
    "json": ("corrupt.json", b"{not valid json,,,"),
}


@pytest.mark.parametrize("kind", sorted(CORRUPT_FILES))
def test_a_corrupt_file_never_sits_at_indexing_forever(
    client: TestClient, analyst: str, kind: str
) -> None:
    """The half of §6.6 that holds: every upload reaches a terminal status."""
    name, payload = CORRUPT_FILES[kind]
    job = upload(client, analyst, [(name, payload)])
    assert job.status_code == 202, job.text
    final = poll_status(client, analyst, job.json()["dataset_id"], timeout=60)
    assert final["status"] in ("ready", "failed"), (
        f"a corrupt {kind} upload is still at {final['status']!r}: {final}"
    )


@pytest.mark.parametrize("kind", sorted(CORRUPT_FILES))
def test_a_corrupt_file_ends_at_failed_with_a_reason(
    client: TestClient, analyst: str, kind: str
) -> None:
    """A file the system could not read must say so, not report success.

    §6.6 is about never leaving a dataset mid-flight, but the rule it serves is
    that a failure is visible. A dataset that reports ``ready`` over an
    unreadable file is the same lie with a nicer colour: the row says indexed,
    the chunk count says real content, and what was actually embedded is the
    parser's own error message.
    """
    name, payload = CORRUPT_FILES[kind]
    job = upload(client, analyst, [(name, payload)])
    dataset_id = job.json()["dataset_id"]

    final = poll_status(client, analyst, dataset_id, timeout=60)
    assert final["status"] == "failed", (
        f"a corrupt {kind} upload ended at {final['status']!r}: {final}"
    )

    body = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
    assert body["error"], "a failed dataset carries no reason"
    assert body["chunks"] == 0, "a failed dataset reports chunks it did not index"


def test_an_indexing_crash_is_recorded_as_failed(
    client: TestClient, analyst: str
) -> None:
    """The §6.6 mechanism itself: when the embedding step raises, the dataset
    lands at ``failed`` with the reason, rather than at ``indexing`` forever."""
    with pytest.MonkeyPatch.context() as mp:
        def boom(**_kwargs):
            raise RuntimeError("qdrant is unreachable")

        mp.setattr(store, "index_dataset_text", boom)
        job = upload(client, analyst, [("doomed-index.md", b"# Doomed\n\nbody\n")])
        dataset_id = job.json()["dataset_id"]
        final = poll_status(client, analyst, dataset_id, timeout=60)

    assert final["status"] == "failed", final
    body = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
    assert "qdrant is unreachable" in body["error"]
    assert body["chunks"] == 0


def test_a_failed_dataset_is_still_deletable(client: TestClient, analyst: str) -> None:
    """A failure that cannot be cleared leaves the demo with a permanent red row."""
    job = upload(client, analyst, [("corrupt2.xlsx", b"not a workbook at all")]).json()
    poll_status(client, analyst, job["dataset_id"], timeout=60)
    response = client.delete(f"{API}/datasets/{job['dataset_id']}", headers=auth(analyst))
    assert response.status_code == 200, response.text


# --- §6.7 — no "try again" on a permanent error -----------------------------


def test_an_unsupported_type_is_not_offered_a_retry(
    client: TestClient, analyst: str
) -> None:
    response = upload(client, analyst, [("virus.exe", b"MZ")])
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["details"]["retryable"] is False, (
        "an unsupported file type is permanent; marking it retryable makes the UI offer "
        "a 'Try again' that can never succeed"
    )
    assert "try again" not in error["message"].lower()
    assert "Retry-After" not in response.headers


def test_an_oversized_file_is_not_offered_a_retry(client: TestClient, analyst: str) -> None:
    response = upload(client, analyst, [("huge.txt", b"x" * (store.MAX_FILE_BYTES + 1))])
    assert response.status_code == 413
    assert response.json()["error"]["details"]["retryable"] is False
    assert "Retry-After" not in response.headers
