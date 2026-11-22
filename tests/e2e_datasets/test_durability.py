"""Phase-2 claim 1 — the dataset list survives a restart, and tells the truth
about what survived with it.

Two separate claims, and the second is the one with teeth:

* the list is still there after the process goes away (the registry is durable);
* every row in it still agrees with what the vector store actually holds.

The second matters more because the failure mode is quieter. A registry that
reloads a row saying ``ready, 42 chunks`` when the collection holds none of
them has not preserved data, it has preserved a *claim* about data — and that
is contract §3.6 ("never ``ready`` over an empty or unreadable index") wearing
the uniform of a fixed bug. The two disagreements this module drives at are the
ones a real restart produces: points deleted out of band, and an indexing job
killed mid-flight.

A restart is simulated by discarding the process-local registry and building a
new one over the same file (``datasets_store.set_registry``), which is exactly
what a fresh process does on boot. The chunk counter is supplied by the test
rather than read from Qdrant, so "what the store holds" is an input and the
assertions are about the reconciliation logic rather than about whatever was
last indexed on this machine. Nothing in this module opens a socket, and
nothing in it writes outside ``tmp_path``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the services/api environment")
pytest.importorskip("sqlglot", reason="needs the services/api environment")

from app.api import datasets_store as store  # noqa: E402
from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from .helpers import ANALYST, API, auth, login, poll_status, upload  # noqa: E402

REGISTRY_MECHANISM = "app.api.datasets_store.DatasetRegistry"

#: The mechanism under test, by name, so a skip says "not built yet" and a
#: failure says "built wrong" — they are different reports.
_MISSING = [
    name
    for name in ("DatasetRegistry", "set_registry", "registry")
    if not hasattr(store, name)
]
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=(
        f"the durable registry is not built yet: {REGISTRY_MECHANISM} is missing "
        f"{', '.join(_MISSING)}"
    ),
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry" / "datasets.json"


@pytest.fixture
def client(tmp_path: Path, registry_path: Path) -> Iterator[TestClient]:
    """An API whose registry is a real file under ``tmp_path``.

    The indexing seam is stubbed to a fixed chunk count: this module is about
    what the registry remembers and what it then claims, so "how many points
    are in Qdrant" has to be something the test states, not something it
    inherits.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RETRIEVER", "qdrant")  # or the upload route refuses, correctly
        mp.setattr(store, "index_dataset_text", lambda **kw: 4)
        mp.setattr(store, "delete_dataset_points", lambda dataset_id: 4)
        store.set_registry(registry_path)
        store.set_data_dir(tmp_path / "uploads")
        reset_caches()
        with TestClient(create_app()) as test_client:
            yield test_client
        store.set_data_dir(None)
        store.set_registry(None)


@pytest.fixture
def analyst(client: TestClient) -> str:
    return login(client, *ANALYST)


def restart(registry_path: Path, counts: object) -> store.DatasetRegistry:
    """Throw the process-local registry away and boot a new one over the file.

    ``counts`` is the chunk counter the new process reconciles against: a
    callable ``dataset_id -> int``, or one that raises to stand for a vector
    store that cannot be reached. Reconciliation is driven explicitly, rather
    than left to the lazy pass in ``registry()``, so this test never reaches
    for the real Qdrant.
    """
    fresh = store.set_registry(registry_path)
    fresh.reconcile(counts)
    return fresh


def _write_registry(path: Path, records: list[dict]) -> None:
    """Hand-write a registry file — a previous process's leftovers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _record(**overrides) -> dict:
    base = {
        "id": "ds_restart000001",
        "name": "before_the_restart.md",
        "kind": "document",
        "status": "ready",
        "bytes": 2048,
        "chunks": 4,
        "redactions": 0,
        "created_at": "2026-11-22T09:00:00Z",
        "error": None,
        "progress": 1.0,
        "message": "Indexed 4 chunk(s)",
        "path": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Durability itself
# --------------------------------------------------------------------------
def test_the_registry_says_honestly_whether_it_survives_a_restart() -> None:
    """``durable`` is the claim the rest of this module relies on, so it has to
    be true rather than optimistic: a memory registry must admit it."""
    assert store.DatasetRegistry(None).durable is False
    assert store.DatasetRegistry(None).backend == "memory"


def test_a_file_backed_registry_is_durable(registry_path: Path) -> None:
    durable = store.DatasetRegistry(registry_path)
    assert durable.durable is True
    assert durable.path == registry_path


def test_a_test_run_cannot_write_to_the_maintainers_own_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§2, in the shape it takes for this store.

    The shared Qdrant is not the only state on this machine that a test run can
    stamp on. The default registry path is ``data/datasets.json`` in the repo —
    the maintainer's real dataset list — so a suite that fell back to the
    default would overwrite it, and ``reset_state()`` would empty it.
    """
    monkeypatch.delenv("DATASETS_STORE_PATH", raising=False)
    store.set_registry(None)
    try:
        assert store.registry().durable is False, (
            "a pytest run built a durable registry with no DATASETS_STORE_PATH set, so "
            f"it is writing to {store._default_registry_path()} — the developer's own "
            "dataset list"
        )
    finally:
        store.set_registry(None)


def test_a_dataset_uploaded_before_a_restart_is_still_listed(
    client: TestClient, analyst: str, registry_path: Path
) -> None:
    """The headline claim. Upload, restart, and the row is still there — with
    the same name, size and chunk count, not a placeholder."""
    job = upload(client, analyst, [("survivor.md", b"# Survivor\n\nstill here\n")])
    assert job.status_code == 202, job.text
    dataset_id = job.json()["dataset_id"]
    assert poll_status(client, analyst, dataset_id)["status"] == "ready"
    before = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()

    restart(registry_path, lambda _id: 4)

    listed = client.get(f"{API}/datasets", headers=auth(analyst)).json()
    assert [d["id"] for d in listed] == [dataset_id], (
        "the dataset list did not survive the restart: the raw file and the vector "
        f"points are still there and the registry lists {listed}"
    )
    after = client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()
    for field in ("name", "kind", "bytes", "chunks", "redactions", "status"):
        assert after[field] == before[field], (
            f"{field} changed across the restart: {before[field]!r} -> {after[field]!r}"
        )


def test_a_restarted_dataset_is_not_left_mid_progress(
    client: TestClient, analyst: str, registry_path: Path
) -> None:
    """§3.8. A ready dataset that reloads showing 45% has a bar describing work
    that finished before the process it belonged to died."""
    job = upload(client, analyst, [("done.md", b"# Done\n\nbody\n")])
    dataset_id = job.json()["dataset_id"]
    poll_status(client, analyst, dataset_id)

    restart(registry_path, lambda _id: 4)

    status = client.get(f"{API}/datasets/{dataset_id}/status", headers=auth(analyst)).json()
    assert status["status"] == "ready"
    assert status["progress"] == pytest.approx(1.0), (
        f"a ready dataset reloaded at progress={status['progress']}, so the UI shows a "
        "bar still in flight for an index that is complete"
    )


def test_the_registry_file_is_a_complete_json_document_with_no_debris(
    client: TestClient, analyst: str, registry_path: Path
) -> None:
    """tmp-then-rename, asserted rather than assumed: a truncated registry is
    the one way a durable list loses more than an in-process one."""
    upload(client, analyst, [("a.md", b"# A\n")])
    upload(client, analyst, [("b.md", b"# B\n")])
    poll_status(client, analyst, client.get(f"{API}/datasets", headers=auth(analyst)).json()[0]["id"])

    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    assert isinstance(raw, list) and len(raw) == 2, raw
    leftovers = sorted(p.name for p in registry_path.parent.iterdir() if p != registry_path)
    assert leftovers == [], f"the registry write left debris behind: {leftovers}"


def test_a_registry_row_never_carries_a_server_path_onto_the_wire(
    client: TestClient, analyst: str, registry_path: Path
) -> None:
    """The durable file has to hold ``path`` (a re-index needs it) while the API
    must not serve it. Persisting a field is how it leaks."""
    upload(client, analyst, [("private.md", b"# P\n")])
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    assert raw[0].get("path"), "the registry forgot where the raw upload is"
    served = client.get(f"{API}/datasets", headers=auth(analyst)).json()[0]
    assert "path" not in served, f"a server filesystem path reached the client: {served}"


# --------------------------------------------------------------------------
# §3.6 — the two disagreements a restart actually produces
# --------------------------------------------------------------------------
def test_a_dataset_recorded_ready_whose_points_are_gone_stops_claiming_ready(
    registry_path: Path,
) -> None:
    """Disagreement one, and the dishonest one.

    The row says indexed with a real chunk count; the collection holds nothing
    for it. Reloading that row unchanged means the UI shows a green badge over
    an index that cannot return a single chunk, and a question about the file
    gets "I found nothing" beside a row that says it is ready. §3.6.
    """
    _write_registry(registry_path, [_record(status="ready", chunks=4)])

    fresh = restart(registry_path, lambda _id: 0)
    row = fresh.get("ds_restart000001")

    assert row is not None, "the dataset vanished instead of being corrected"
    assert row.status != "ready", (
        "a dataset whose vector points are gone reloaded as 'ready' with "
        f"chunks={row.chunks}: the badge claims an index that holds nothing"
    )
    assert row.status == "failed", f"expected 'failed', got {row.status!r}"
    assert row.chunks == 0, f"a dataset with no points reports chunks={row.chunks}"
    assert row.error, "the correction carries no reason the reader can act on"


def test_a_dataset_left_at_indexing_by_a_killed_process_becomes_failed(
    registry_path: Path,
) -> None:
    """Disagreement two. No worker survived the restart, so nothing is coming to
    finish this job: ``indexing`` for ever is the one state §3.6 rules out."""
    _write_registry(
        registry_path,
        [_record(status="indexing", chunks=0, progress=0.45, message="Embedding")],
    )

    fresh = restart(registry_path, lambda _id: 0)
    row = fresh.get("ds_restart000001")

    assert row is not None
    assert row.status == "failed", (
        f"a dataset killed mid-index reloaded at {row.status!r}; no worker survived the "
        "restart, so that bar will never move again"
    )
    assert row.error, "an interrupted index gives the reader no reason"
    assert row.progress == pytest.approx(1.0), (
        f"a terminal dataset reports progress={row.progress}, so the UI keeps animating"
    )


@pytest.mark.parametrize("stale_status", ["pending", "indexing"])
def test_an_interrupted_index_that_actually_finished_is_promoted_not_failed(
    registry_path: Path, stale_status: str
) -> None:
    """The mirror image, and the reason reconciliation reads the store rather
    than assuming: the embed completed and only the bookkeeping was lost.
    Failing it would throw away work that is sitting in the collection."""
    _write_registry(registry_path, [_record(status=stale_status, chunks=0)])

    row = restart(registry_path, lambda _id: 11).get("ds_restart000001")

    assert row is not None
    assert row.status == "ready", (
        f"{stale_status!r} with 11 points in the store became {row.status!r}; the work is "
        "done and the registry is discarding it"
    )
    assert row.chunks == 11


def test_reconciliation_reports_the_live_count_not_the_remembered_one(
    registry_path: Path,
) -> None:
    """§3.8. ``chunks`` is shown to the reader as a count of what is searchable,
    so it has to be the store's number and not the last process's belief."""
    _write_registry(registry_path, [_record(status="ready", chunks=4)])

    row = restart(registry_path, lambda _id: 9).get("ds_restart000001")

    assert row is not None and row.chunks == 9, (
        f"the registry reports chunks={row.chunks if row else None} while the store holds 9"
    )


def test_an_unreachable_vector_store_is_not_reported_as_data_loss(
    registry_path: Path,
) -> None:
    """The other half of §3.6, and the easier one to get wrong.

    "I could not ask" is not "your data is gone". If Qdrant is down at boot, a
    reconciliation that reads the failure as zero points marks every dataset on
    the machine ``failed`` with a reason that is false, and the files and
    vectors are all still there. An indexed dataset must be left alone.
    """
    def unreachable(_dataset_id: str) -> int:
        raise ConnectionError("qdrant is not reachable at 127.0.0.1:6333")

    _write_registry(registry_path, [_record(status="ready", chunks=4)])
    row = restart(registry_path, unreachable).get("ds_restart000001")

    assert row is not None
    assert row.status == "ready", (
        f"an unreachable vector store turned an indexed dataset into {row.status!r}: "
        f"error={row.error!r}. Nothing was lost; the count could not be read."
    )
    assert row.chunks == 4, "the remembered count was discarded on an unknown, not a zero"


def test_an_unreachable_vector_store_still_resolves_a_mid_flight_dataset(
    registry_path: Path,
) -> None:
    """...but ``indexing`` is still resolved, because that conclusion does not
    depend on the count: the worker is gone either way."""
    def unreachable(_dataset_id: str) -> int:
        raise ConnectionError("qdrant is not reachable")

    _write_registry(registry_path, [_record(status="indexing", chunks=0)])
    row = restart(registry_path, unreachable).get("ds_restart000001")

    assert row is not None and row.status == "failed", (
        f"a dataset stuck at 'indexing' stayed at {row.status if row else None!r} because "
        "the count could not be read, but no worker survived the restart regardless"
    )


def test_a_failed_dataset_with_orphaned_points_keeps_a_deletable_count(
    registry_path: Path,
) -> None:
    """A failed row with points still in the store must report them, or DELETE
    skips the vector cleanup (it only runs when ``chunks`` > 0) and leaves them
    searchable for ever — an orphan the reader cannot see or remove."""
    _write_registry(registry_path, [_record(status="failed", chunks=0, error="boom")])

    row = restart(registry_path, lambda _id: 6).get("ds_restart000001")

    assert row is not None
    assert row.status == "failed", "a failed dataset was quietly promoted"
    assert row.chunks == 6, (
        f"chunks={row.chunks} while 6 orphaned points remain, so DELETE will not remove them"
    )


def test_reconciliation_is_idempotent(registry_path: Path) -> None:
    """Boot twice, change nothing the second time. A pass that keeps rewriting
    the same rows is one that logs a correction on every restart."""
    _write_registry(registry_path, [_record(status="ready", chunks=4)])
    fresh = restart(registry_path, lambda _id: 0)
    assert fresh.reconcile(lambda _id: 0) == [], "the second pass changed rows again"


def test_reconciliation_never_deletes_anything(registry_path: Path) -> None:
    """§2. Boot is a read of the vector store and nothing else. A reconciliation
    that "cleaned up" on the way up would be a destructive call against a shared
    instance, performed before anyone is watching."""
    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("reconciliation attempted a destructive Qdrant call")

    _write_registry(
        registry_path,
        [
            _record(id="ds_a", status="ready", chunks=4),
            _record(id="ds_b", status="indexing", chunks=0),
            _record(id="ds_c", status="failed", chunks=0),
        ],
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store, "delete_dataset_points", refuse)
        mp.setattr(store, "index_dataset_text", refuse)
        restart(registry_path, lambda _id: 0)


def test_a_restart_does_not_resurrect_a_deleted_dataset(
    client: TestClient, analyst: str, registry_path: Path
) -> None:
    """Delete, restart, and it stays deleted. A durable registry whose removal
    was only in memory brings the row back with its files already gone."""
    job = upload(client, analyst, [("goner.md", b"# Goner\n")])
    dataset_id = job.json()["dataset_id"]
    poll_status(client, analyst, dataset_id)
    assert client.delete(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).status_code == 200

    restart(registry_path, lambda _id: 0)

    listed = client.get(f"{API}/datasets", headers=auth(analyst)).json()
    assert listed == [], f"a deleted dataset came back after the restart: {listed}"


# --------------------------------------------------------------------------
# The registry file as an input nobody validated
# --------------------------------------------------------------------------
def test_a_registry_row_that_is_not_a_dataset_does_not_take_the_list_with_it(
    registry_path: Path,
) -> None:
    """One unreadable row must not cost the reader the other nine."""
    _write_registry(
        registry_path,
        [_record(id="ds_good"), {"id": "ds_bad", "name": 7}, _record(id="ds_good2")],
    )
    fresh = store.set_registry(registry_path)
    ids = sorted(d.id for d in fresh.list_all())
    assert "ds_good" in ids and "ds_good2" in ids, (
        f"one malformed row emptied the whole dataset list: {ids}"
    )


def test_a_corrupt_registry_is_not_silently_an_empty_dataset_list(
    registry_path: Path,
) -> None:
    """A registry that cannot be read is an unknown list, not an empty one.

    Either the reader is told the list could not be loaded, or the datasets are
    recovered from the raw uploads that are still on disk. Presenting an empty
    list is the one option that is untrue.
    """
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text('[{"id": "ds_truncat', encoding="utf-8")

    fresh = store.set_registry(registry_path)

    assert fresh.list_all() == []  # the current behaviour, which is the problem
    # ...and nothing anywhere says so. A loaded-but-unreadable registry needs a
    # signal the API can surface; any of these would do.
    signal = (
        getattr(fresh, "load_error", None)
        or getattr(fresh, "unreadable", None)
        or getattr(fresh, "degraded", None)
    )
    assert signal, (
        "the registry could not be read and reports no error, so the dataset list is "
        "served as empty while the uploads and their vectors are still on disk"
    )
