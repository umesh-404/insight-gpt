"""Phase-2 claim 1, at full fidelity — skipped unless the live stack is up.

``test_durability.py`` drives reconciliation with a chunk counter the test
supplies, which is how its disagreements are made precise. This module does the
opposite: it embeds a real file into a real Qdrant, restarts the registry
against the real collection, and asks whether the dataset is still listed, still
``ready``, and still citeable. Nothing here states what the vector store holds —
it reads it.

Enabling it, as in ``test_live_roundtrip.py``::

    RETRIEVAL_LIVE=1 uv run --project services/api --extra real \\
        pytest -q tests/e2e_datasets/test_live_phase2.py

**Contract §2.** Writes go to ``insightgpt_test_phase2_<pid>``, created by this
module and dropped by it through the guard that asserts the prefix first. The
shared instance's ``code`` / ``docs`` / ``memory`` are counted before and after
and asserted not to have lost points. ``insightgpt_documents`` is never written
to or dropped. The uploaded-table DuckDB store is a second store with the same
rule: this module points ``DATASETS_DB_PATH`` at ``tmp_path`` so no
``upload_ds_*`` table is ever created in the maintainer's.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from .qdrant_safety import (
    FOREIGN_COLLECTIONS,
    drop_test_collection,
    qdrant_url,
    scratch_collection_name,
)

httpx = pytest.importorskip("httpx")

SCRATCH = scratch_collection_name("phase2")


def _service_up(url: str) -> bool:
    try:
        httpx.get(url, timeout=3).raise_for_status()
        return True
    except (httpx.HTTPError, OSError):
        return False


def _why_skipped() -> str | None:
    if os.environ.get("RETRIEVAL_LIVE") != "1":
        return "set RETRIEVAL_LIVE=1 to enable the live phase-2 test"
    for module in ("fastapi", "retrieval", "qdrant_client"):
        try:
            __import__(module)
        except ImportError:
            return (
                f"{module} is not installed in this environment; run with "
                "`uv run --project services/api --extra real`"
            )
    try:
        from app.api.datasets_store import registry  # noqa: F401
    except ImportError:
        return (
            "app.api.datasets_store has no registry() — the durable registry does "
            "not exist yet (not built, as opposed to built wrong)"
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
    os.environ["QDRANT_COLLECTION"] = SCRATCH
    os.environ["RETRIEVER"] = "qdrant"

from .helpers import (  # noqa: E402
    ANALYST,
    API,
    LIGHTHOUSE_FILE,
    QUARRY_FILE,
    QUARRY_ROWS,
    QUARRY_TOTAL_REVENUE,
    auth,
    citation_mentions,
    fixture_bytes,
    login,
    poll_status,
    reported_figures,
    states_figure,
    upload,
)


def _points_in(collection: str) -> int:
    response = httpx.get(f"{qdrant_url()}/collections/{collection}", timeout=10)
    if response.status_code == 404:
        return -1
    response.raise_for_status()
    return int(response.json()["result"]["points_count"] or 0)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def foreign_before() -> dict[str, int]:
    """Point counts of the three collections that are not ours (§2)."""
    return {name: _points_in(name) for name in FOREIGN_COLLECTIONS}


@pytest.fixture(scope="module")
def registry_path(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("live-registry") / "datasets.json"


@pytest.fixture(scope="module")
def client(
    foreign_before: dict[str, int],
    registry_path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Any]:
    from app.api import datasets_store as store
    from app.api.deps import reset_caches
    from app.api.main import create_app
    from app.warehouse.datasets_db import DatasetsDB, set_datasets_db
    from fastapi.testclient import TestClient
    from retrieval.config import get_config

    get_config.cache_clear()
    assert get_config().collection == SCRATCH, (
        "the scratch collection did not take effect; refusing to run against "
        f"{get_config().collection!r}"
    )
    uploads = tmp_path_factory.mktemp("live-uploads")
    scratch_db = DatasetsDB(uploads / "datasets.duckdb")
    set_datasets_db(scratch_db)
    store.set_registry(registry_path)
    store.set_data_dir(uploads / "files")
    reset_caches()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        store.set_data_dir(None)
        store.set_registry(None)
        scratch_db.close()
        set_datasets_db(None)
        # Drops ONLY insightgpt_test_phase2_<pid>; the name is asserted inside.
        drop_test_collection(SCRATCH)
        get_config.cache_clear()


@pytest.fixture(scope="module")
def analyst(client: Any) -> str:
    return login(client, *ANALYST)


@pytest.fixture(scope="module")
def indexed(client: Any, analyst: str) -> dict[str, dict[str, Any]]:
    """A document and a table, both really embedded into the scratch collection."""
    out: dict[str, dict[str, Any]] = {}
    for name in (LIGHTHOUSE_FILE, QUARRY_FILE):
        response = upload(client, analyst, [(name, fixture_bytes(name))])
        assert response.status_code == 202, response.text
        dataset_id = response.json()["dataset_id"]
        job = poll_status(client, analyst, dataset_id, timeout=600)
        body = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
        assert job["status"] == "ready", f"{name} did not index: {job} / {body}"
        assert body["chunks"] > 0
        out[name] = body
    return out


# --------------------------------------------------------------------------
# §2 first, because nothing else matters if this failed
# --------------------------------------------------------------------------
def test_the_other_projects_collections_did_not_lose_points(
    indexed: dict[str, dict[str, Any]], foreign_before: dict[str, int]
) -> None:
    """Counted before and after a run that really wrote to this instance.

    Growth is expected — ``memory`` and ``code`` belong to a live tool on this
    machine that indexes while we work — so the assertion is that nothing was
    *lost*, which is the irreversible direction.
    """
    for name, before in foreign_before.items():
        after = _points_in(name)
        assert after >= before, (
            f"the foreign collection {name!r} went from {before} points to {after}: "
            "this run destroyed another project's data"
        )


def test_the_production_collection_was_not_written_to(
    indexed: dict[str, dict[str, Any]]
) -> None:
    """Uploads landed in the scratch collection, so ``insightgpt_documents``
    must not hold any of these dataset ids."""
    if _points_in("insightgpt_documents") < 0:
        pytest.skip("insightgpt_documents does not exist on this instance")
    for name, dataset in indexed.items():
        response = httpx.post(
            f"{qdrant_url()}/collections/insightgpt_documents/points/count",
            json={
                "filter": {"must": [{"key": "dataset_id", "match": {"value": dataset["id"]}}]},
                "exact": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        count = response.json()["result"]["count"]
        assert count == 0, (
            f"{name} wrote {count} point(s) into the production collection during a "
            "scratch run"
        )


# --------------------------------------------------------------------------
# The claim: a restart against the real store
# --------------------------------------------------------------------------
def test_a_real_restart_keeps_the_dataset_list_and_its_ready_badge(
    client: Any, analyst: str, indexed: dict[str, dict[str, Any]], registry_path
) -> None:
    """The headline claim, with nothing simulated but the process boundary.

    The points are genuinely in the collection, so reconciliation has to read
    them and leave the rows alone. A row downgraded here is reconciliation
    calling real data missing; a row that vanishes is the phase-1 bug.
    """
    from app.api import datasets_store as store

    expected = {d["id"]: d for d in indexed.values()}

    fresh = store.set_registry(registry_path)
    changed = fresh.reconcile()  # the real Qdrant counter, not a stub

    listed = client.get(f"{API}/datasets", headers=auth(analyst)).json()
    by_id = {row["id"]: row for row in listed}
    missing = sorted(set(expected) - set(by_id))
    assert not missing, (
        f"{len(missing)} dataset(s) did not survive the restart although their points "
        f"are in {SCRATCH}: {missing}. Listed: {sorted(by_id)}"
    )
    for dataset_id, before in expected.items():
        after = by_id[dataset_id]
        assert after["status"] == "ready", (
            f"{before['name']} reloaded as {after['status']!r} (error={after.get('error')!r}) "
            f"while {_points_in(SCRATCH)} point(s) are in the collection; "
            "reconciliation is reporting live data as lost"
        )
        assert after["chunks"] == before["chunks"], (
            f"{before['name']}: chunks changed from {before['chunks']} to "
            f"{after['chunks']} across the restart"
        )
        assert after["name"] == before["name"] and after["bytes"] == before["bytes"]
    assert changed == [], (
        f"reconciliation corrected rows that were already correct: {changed}"
    )


def test_an_answer_still_cites_the_dataset_after_the_restart(
    client: Any, analyst: str, indexed: dict[str, dict[str, Any]], registry_path
) -> None:
    """Surviving in the list is not enough: the point of the list is that the
    data behind it is still reachable."""
    from app.api import datasets_store as store

    store.set_registry(registry_path).reconcile()

    response = client.post(
        f"{API}/ask",
        json={"question": f"Summarize {LIGHTHOUSE_FILE} and the keeper's log", "stream": False},
        headers={**auth(analyst), "Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert envelope.get("citations"), (
        f"after the restart the dataset is listed but nothing cites it: "
        f"route={envelope.get('route')} answer={envelope.get('answer', '')[:200]!r}"
    )
    assert citation_mentions(envelope, LIGHTHOUSE_FILE), (
        f"no citation names {LIGHTHOUSE_FILE}: {envelope['citations']}"
    )


def test_a_tabular_upload_answers_its_total_against_the_real_stack(
    client: Any, analyst: str, indexed: dict[str, dict[str, Any]]
) -> None:
    """Claim 2, live. The expected figure is computed from the fixture, so this
    is the same assertion the offline module makes, against the real store."""
    response = client.post(
        f"{API}/ask",
        json={"question": f"What is the total revenue in {QUARRY_FILE}?", "stream": False},
        headers={**auth(analyst), "Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    envelope = response.json()
    if envelope.get("abstained"):
        pytest.fail(
            "the live stack declined a whole-table aggregate it can compute; the "
            f"figure is {QUARRY_TOTAL_REVENUE:,.2f}. answer={envelope['answer']!r}"
        )
    assert states_figure(envelope["answer"], QUARRY_TOTAL_REVENUE), (
        f"expected {QUARRY_TOTAL_REVENUE:,.2f} over {QUARRY_ROWS} rows; the answer "
        f"reports {reported_figures(envelope['answer'])}.\nanswer={envelope['answer']!r}"
    )
    joined = " ".join(envelope.get("sql") or []).lower()
    for table in ("fact_order_items", "dim_product", "dim_date"):
        assert table not in joined, (
            f"the query over an uploaded table touched the governed warehouse: {joined}"
        )
    assert QUARRY_FILE.lower() in envelope["answer"].lower() or citation_mentions(
        envelope, QUARRY_FILE
    ), "the figure names no dataset, so it reads as a warehouse number (§3.2)"
