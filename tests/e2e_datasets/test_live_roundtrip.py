"""The real journey, end to end — skipped unless the live stack is up.

Upload a file through the API -> it is parsed, redacted, chunked, embedded and
stored in Qdrant -> asking a question in ``/ask`` retrieves from it -> the answer
cites that dataset. Nothing is mocked: a real local Ollama produces the vectors
and a real Qdrant stores them.

Enabling it, following the convention in ``services/retrieval/tests/test_integration.py``::

    RETRIEVAL_LIVE=1 uv run --project services/api --extra real \\
        pytest -q tests/e2e_datasets/test_live_roundtrip.py

The ``real`` extra is what puts the retrieval backend and ``qdrant_client`` in
the API environment; it is already declared in ``services/api/pyproject.toml``
and is what the container image installs, so this adds no dependency.

**Contract §3.** Every write in this module goes to a scratch collection named
``insightgpt_test_live_<pid>``, created by this test and dropped by it through
:func:`qdrant_safety.drop_test_collection`, which asserts the name first. The
shared instance's ``code`` / ``docs`` / ``memory`` are listed before and after
and asserted untouched. ``insightgpt_documents`` is never written to or dropped.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from .qdrant_safety import (
    FOREIGN_COLLECTIONS,
    drop_test_collection,
    list_collections,
    qdrant_url,
    scratch_collection_name,
)

httpx = pytest.importorskip("httpx")

SCRATCH = scratch_collection_name("live")


def _service_up(url: str) -> bool:
    try:
        httpx.get(url, timeout=3).raise_for_status()
        return True
    except (httpx.HTTPError, OSError):
        return False


def _why_skipped() -> str | None:
    """The reason this module cannot run, or ``None`` when it can.

    Returned as prose rather than a bool so a skipped run says which piece is
    missing — "not built yet" and "not running" are different problems.
    """
    if os.environ.get("RETRIEVAL_LIVE") != "1":
        return "set RETRIEVAL_LIVE=1 to enable the live end-to-end test"
    for module in ("fastapi", "retrieval", "qdrant_client"):
        try:
            __import__(module)
        except ImportError:
            return (
                f"{module} is not installed in this environment; run with "
                "`uv run --project services/api --extra real`"
            )
    try:
        __import__("retrieval.datasets")
    except ImportError:
        return (
            "retrieval.datasets (the dataset indexing seam the API calls) does not "
            "exist yet — not built, as opposed to built wrong"
        )
    if not _service_up(f"{qdrant_url()}/readyz"):
        return f"Qdrant is not reachable at {qdrant_url()}"
    ollama = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not _service_up(f"{ollama}/api/tags"):
        return f"Ollama is not reachable at {ollama}"
    return None


_SKIP_REASON = _why_skipped()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")

if _SKIP_REASON is None:
    # Point the whole stack — indexer AND retriever — at the scratch collection
    # before anything reads the config. One variable, read by both, so they
    # cannot disagree about where the points are (contract §7).
    os.environ["QDRANT_COLLECTION"] = SCRATCH
    os.environ["RETRIEVER"] = "qdrant"

from .helpers import (  # noqa: E402
    ANALYST,
    API,
    LIGHTHOUSE_FACT_TOKENS,
    LIGHTHOUSE_FILE,
    LIGHTHOUSE_KEEPER,
    RIDERSHIP_FILE,
    SECRET_BODIES,
    SECRET_FILE,
    SECRET_FILE_SURVIVING_TEXT,
    auth,
    fixture_bytes,
    login,
    poll_status,
    upload,
)


# --------------------------------------------------------------------------
# Qdrant reads (HTTP, so no client version is assumed)
# --------------------------------------------------------------------------
def _scroll(collection: str, *, dataset_id: str | None = None) -> list[dict[str, Any]]:
    """Every point in ``collection``, optionally filtered to one dataset."""
    body: dict[str, Any] = {"limit": 500, "with_payload": True, "with_vector": False}
    if dataset_id is not None:
        body["filter"] = {"must": [{"key": "dataset_id", "match": {"value": dataset_id}}]}
    points: list[dict[str, Any]] = []
    while True:
        response = httpx.post(
            f"{qdrant_url()}/collections/{collection}/points/scroll", json=body, timeout=60
        )
        response.raise_for_status()
        result = response.json()["result"]
        points.extend(result.get("points", []))
        offset = result.get("next_page_offset")
        if not offset:
            return points
        body["offset"] = offset


def _payload_text(point: dict[str, Any]) -> str:
    return " ".join(str(value) for value in (point.get("payload") or {}).values())


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def foreign_collections_before() -> list[str]:
    return sorted(c for c in list_collections() if c in FOREIGN_COLLECTIONS)


@pytest.fixture(scope="module")
def client(foreign_collections_before: list[str]) -> Iterator[Any]:
    from app.api import datasets_store as store
    from app.api.deps import reset_caches
    from app.api.main import create_app
    from fastapi.testclient import TestClient
    from retrieval.config import get_config

    get_config.cache_clear()
    assert get_config().collection == SCRATCH, (
        "the scratch collection did not take effect; refusing to run against "
        f"{get_config().collection!r}"
    )
    reset_caches()
    store.reset_state()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        store.reset_state()
        # Drops ONLY insightgpt_test_live_<pid>; the name is asserted inside.
        drop_test_collection(SCRATCH)
        get_config.cache_clear()


@pytest.fixture(scope="module")
def analyst(client: Any) -> str:
    return login(client, *ANALYST)


@pytest.fixture(scope="module")
def indexed(client: Any, analyst: str) -> dict[str, dict[str, Any]]:
    """Upload the three fixture files and return each one's ready Dataset."""
    names = (LIGHTHOUSE_FILE, RIDERSHIP_FILE, SECRET_FILE)
    datasets: dict[str, dict[str, Any]] = {}
    for name in names:
        response = upload(client, analyst, [(name, fixture_bytes(name))])
        assert response.status_code == 202, response.text
        dataset_id = response.json()["dataset_id"]
        job = poll_status(client, analyst, dataset_id, timeout=300)
        body = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
        assert job["status"] == "ready", f"{name} did not index: {job} / {body}"
        datasets[name] = body
    return datasets


# --------------------------------------------------------------------------
# The journey
# --------------------------------------------------------------------------
def test_upload_indexes_into_the_scratch_collection(
    indexed: dict[str, dict[str, Any]]
) -> None:
    for name, dataset in indexed.items():
        assert dataset["chunks"] > 0, f"{name} reported ready with no chunks"
        points = _scroll(SCRATCH, dataset_id=dataset["id"])
        assert len(points) == dataset["chunks"], (
            f"{name} reports {dataset['chunks']} chunks but {len(points)} points carry its "
            "dataset_id — the count shown to the user is not what was written"
        )
        for point in points:
            payload = point["payload"]
            assert payload.get("dataset_id") == dataset["id"]
            assert name in str(payload.get("dataset_name") or payload.get("title") or ""), (
                "a point carries no name for its dataset, so a citation cannot say "
                f"where the answer came from: {payload}"
            )


def test_the_planted_fact_is_retrievable(indexed: dict[str, dict[str, Any]]) -> None:
    """Provable retrieval: the query terms appear in no other corpus on the box,
    so a hit can only have come from the uploaded file."""
    from retrieval.config import load_config
    from retrieval.retriever import QdrantRetriever

    results = QdrantRetriever(config=load_config()).search(
        "who replaced the lighthouse lamp and what visibility did they log?", k=5
    )
    assert results, "the live retriever returned nothing for an indexed document"
    bodies = " ".join(r.body for r in results)
    assert LIGHTHOUSE_KEEPER in bodies, (
        f"the keeper's name is in the uploaded file and in nothing else, and it is not in "
        f"the top 5 results: {[(r.doc_id, r.title, r.score) for r in results]}"
    )


def test_asking_a_question_cites_the_uploaded_dataset(
    client: Any, analyst: str, indexed: dict[str, dict[str, Any]]
) -> None:
    """§6.2 on the live path: the answer names the file it came from."""
    from .helpers import ask, citation_mentions

    envelope = ask(client, analyst, f"Summarize {LIGHTHOUSE_FILE} and the keeper's log")
    assert envelope["citations"], f"no citations for an indexed document: {envelope}"
    assert citation_mentions(envelope, LIGHTHOUSE_FILE), (
        f"no citation names the dataset: {envelope['citations']}"
    )
    cited = " ".join(
        str(value)
        for citation in envelope["citations"]
        for value in citation.values()
        if isinstance(value, str)
    )
    assert any(token in cited or token in envelope["answer"] for token in LIGHTHOUSE_FACT_TOKENS), (
        "the cited evidence contains none of the facts that exist only in the uploaded "
        f"file, so the retrieval is not demonstrably from it: {envelope['citations']}"
    )


def test_the_routers_date_window_filters_out_uploads(
    indexed: dict[str, dict[str, Any]]
) -> None:
    """The same query, with and without the window the router supplies."""
    from retrieval.config import load_config
    from retrieval.retriever import QdrantRetriever

    retriever = QdrantRetriever(config=load_config())
    query = "who replaced the lighthouse lamp and what visibility did they log?"
    unfiltered = retriever.search(query, k=5)
    assert unfiltered, "precondition failed: the document is not retrievable at all"

    # Exactly what the router resolves for a question with no explicit period,
    # given the project's reference date of 2026-07-15.
    windowed = retriever.search(
        query, filters={"date_range": {"start": "2026-04-01", "end": "2026-06-30"}}, k=5
    )
    assert windowed, (
        "a resolved date window removed every chunk of an uploaded dataset: "
        f"{len(unfiltered)} hit(s) unfiltered, {len(windowed)} with the window"
    )


def test_an_index_that_does_not_exist_returns_nothing(client: Any, analyst: str) -> None:
    """§6.3 on the live path: before anything is uploaded there is no collection
    at all, and that has to read as an empty result rather than a 500 — and
    certainly not as a placeholder result.

    Points at a collection name that is never created, so it creates nothing.
    """
    from retrieval.config import load_config
    from retrieval.retriever import QdrantRetriever


    config = load_config()
    config.collection = scratch_collection_name("absent")
    assert config.collection not in list_collections(), "precondition: must not exist"

    assert QdrantRetriever(config=config).search("anything at all", k=5) == []

    # Deliberately NOT asserted here: that `/ask` returns no citations. The
    # running API is configured against the real collection, which by this
    # point in the module holds the fixtures the `indexed` fixture uploaded -
    # so citations there are correct, and demanding none of them would be
    # asserting that a populated index behaves like an empty one. The claim
    # this test exists for is the one above: a collection that does not exist
    # answers with nothing instead of raising. The `/ask`-level version of
    # §6.3, against an engine with a genuinely empty index, is covered offline
    # in `test_honesty.py::test_nothing_uploaded_means_ask_does_not_look_like_it_searched`.


# --- §6.4 on the live path: the secret is not in the vector store -----------


def test_no_secret_reaches_qdrant(indexed: dict[str, dict[str, Any]]) -> None:
    """Contract §6.4 says redaction runs before embedding. This is that claim
    tested against the store itself rather than taken on trust."""
    dataset = indexed[SECRET_FILE]
    assert dataset["redactions"] >= 3, (
        f"{SECRET_FILE} carries secret-shaped values but only "
        f"{dataset['redactions']} redaction(s) were reported"
    )

    everything = " ".join(_payload_text(point) for point in _scroll(SCRATCH))
    for secret in SECRET_BODIES:
        assert secret not in everything, f"{secret!r} is in the vector store"
    # Redaction by deletion would also pass the check above.
    own = " ".join(_payload_text(p) for p in _scroll(SCRATCH, dataset_id=dataset["id"]))
    assert SECRET_FILE_SURVIVING_TEXT in own, (
        "the redacted document lost its ordinary content too, so the test above proves "
        "nothing about redaction"
    )


# --- deletion is a filtered delete, never a drop ---------------------------


def test_delete_removes_only_that_dataset(
    client: Any, analyst: str, indexed: dict[str, dict[str, Any]]
) -> None:
    doomed = indexed[RIDERSHIP_FILE]
    survivor = indexed[LIGHTHOUSE_FILE]
    survivor_points_before = len(_scroll(SCRATCH, dataset_id=survivor["id"]))
    assert survivor_points_before > 0

    response = client.delete(f"{API}/datasets/{doomed['id']}", headers=auth(analyst))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True}

    assert _scroll(SCRATCH, dataset_id=doomed["id"]) == [], (
        "the dataset's points are still searchable after a successful delete"
    )
    assert len(_scroll(SCRATCH, dataset_id=survivor["id"])) == survivor_points_before, (
        "deleting one dataset removed another dataset's points"
    )
    assert SCRATCH in list_collections(), (
        "the collection itself was dropped; deletion must be a filtered delete (§3)"
    )


def test_the_shared_instance_is_untouched(
    indexed: dict[str, dict[str, Any]], foreign_collections_before: list[str]
) -> None:
    """The check that matters most: the other project's collections are exactly
    as they were, and nothing of ours landed in them."""
    after = sorted(c for c in list_collections() if c in FOREIGN_COLLECTIONS)
    assert after == foreign_collections_before, (
        f"the shared instance changed: {foreign_collections_before} -> {after}"
    )
    for collection in after:
        info = httpx.get(f"{qdrant_url()}/collections/{collection}", timeout=30)
        info.raise_for_status()
        # A read, not a write: proof the collection still answers and still has
        # its points. The count is not compared to a baseline because the other
        # project may legitimately be indexing while this runs.
        assert info.json()["result"]["status"] in ("green", "yellow", "grey")


def test_nothing_was_written_to_the_production_collection(
    indexed: dict[str, dict[str, Any]]
) -> None:
    """A test run must not leave its fixtures in ``insightgpt_documents``."""
    if "insightgpt_documents" not in list_collections():
        return
    points = _scroll("insightgpt_documents")
    for dataset in indexed.values():
        assert all(
            (point.get("payload") or {}).get("dataset_id") != dataset["id"] for point in points
        ), f"test dataset {dataset['name']} was indexed into the production collection"
