"""Phase-2 claim 3 — indexing progress corresponds to real work (§3.8).

A bar that moves while nothing happens is a lie told at the exact moment the
reader is deciding whether to wait or reload, and it is the cheapest lie in the
product to tell: a timer and a linear ramp look identical to real progress for
the first thirty seconds. So the assertions here are not "does the number go
up" but "does each step of it correspond to work that has finished":

* the embedder reports once per embedding request, after that request returns;
* the fraction is derived from batches completed out of batches needed, never
  from elapsed time;
* no work means no reports;
* nothing reaches 1.0 before the dataset is terminal;
* the number never goes backwards when the indexer restarts its own 0..1 scale
  inside the worker's slice of the bar.

Offline throughout. The embedding step is a counting stub, so "how much work
happened" is something this file knows exactly rather than something it infers.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="needs the services/api environment")
pytest.importorskip("sqlglot", reason="needs the services/api environment")

from app.api import datasets_store as store  # noqa: E402
from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.routers import datasets as datasets_router  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from .helpers import ANALYST, login, poll_status, upload  # noqa: E402


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RETRIEVER", "qdrant")
        store.set_data_dir(tmp_path / "uploads")
        store.reset_state()
        reset_caches()
        with TestClient(create_app()) as test_client:
            yield test_client
        store.reset_state()
        store.set_data_dir(None)


@pytest.fixture
def analyst(client: TestClient) -> str:
    return login(client, *ANALYST)


# --------------------------------------------------------------------------
# The API end: is a callback handed over at all?
# --------------------------------------------------------------------------
def test_the_indexing_seam_is_given_a_progress_callback(
    client: TestClient, analyst: str
) -> None:
    """The whole claim rests on this one argument.

    Before phase 2 the indexer exposed a progress callback and the caller did
    not pass it, so the bar jumped 0.45 -> 1.0 across the slowest step in the
    product. An assertion that only watched the numbers could not tell that
    apart from an embed that finished quickly.
    """
    seen: dict[str, Any] = {}

    def capture(**kwargs: Any) -> int:
        seen.update(kwargs)
        return 3

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store, "index_dataset_text", capture)
        job = upload(client, analyst, [("wired.md", b"# Wired\n\nbody\n")])
        poll_status(client, analyst, job.json()["dataset_id"])

    assert "progress" in seen, (
        "the indexing seam was called without a progress callback, so nothing the "
        f"indexer reports can reach the bar. kwargs={sorted(seen)}"
    )
    assert callable(seen["progress"])


def _observe_during_index(
    client: TestClient,
    analyst: str,
    filename: str,
    fractions: tuple[float, ...],
    *,
    chunks: int = 32,
) -> list[tuple[str, float]]:
    """``(status, progress)`` as a poll would see it, sampled mid-index.

    Polling the route from the test cannot observe this: Starlette runs a
    ``BackgroundTasks`` job inside the request-response cycle, so with
    ``TestClient`` the POST does not return until indexing has finished and
    every poll afterwards sees a terminal dataset. Reading the registry from
    *inside* the stubbed indexer samples the same state the ``/status`` route
    serves — ``job_of`` reads the same record — at the instants that matter.
    """
    seen: list[tuple[str, float]] = []

    def slow_index(*, progress=None, dataset_id: str = "", **_kwargs: Any) -> int:
        for index, fraction in enumerate(fractions, start=1):
            if progress is not None:
                progress(fraction, f"embedded {index * 8:,} of {chunks:,} chunks")
            record = store.get(dataset_id)
            if record is not None:
                job = store.job_of(record)
                seen.append((job.status, job.progress))
        return chunks

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store, "index_dataset_text", slow_index)
        job_response = upload(client, analyst, [(filename, b"# Body\n\ntext\n")])
        final = poll_status(client, analyst, job_response.json()["dataset_id"])
    assert final["status"] == "ready", final
    seen.append((final["status"], final["progress"]))
    return seen


def test_progress_moves_during_the_embed_rather_than_jumping_at_the_end(
    client: TestClient, analyst: str
) -> None:
    """The gap the old bar sat still in.

    Before phase 2 the sequence was 0.1, 0.3, 0.45, then 1.0 — the embed, which
    is where the minutes go, contributed nothing. At least one value has to
    land strictly inside it, and the values have to be ordered.
    """
    seen = _observe_during_index(
        client, analyst, "slow.md", (0.25, 0.5, 0.75, 1.0)
    )
    values = [value for _, value in seen]

    mid = [value for value in values if 0.45 < value < 1.0]
    assert mid, (
        "progress never took a value between the pre-embed mark (0.45) and done, so "
        f"the bar still jumps across the embed: {values}"
    )
    assert len(set(mid)) > 1, (
        f"the bar moved once and then sat still for the rest of the embed: {values}"
    )
    assert values == sorted(values), f"progress moved backwards during one index: {values}"


def test_progress_does_not_reach_one_before_the_dataset_is_terminal(
    client: TestClient, analyst: str
) -> None:
    """§3.8. A full bar over a job still running is the clearest false claim the
    indexing UI can make, because the reader stops waiting.

    The indexer's own scale legitimately reaches 1.0 when the last batch comes
    back — the points are not in the store at that moment — so the mapping has
    to hold the bar short of full until the dataset is terminal.
    """
    seen = _observe_during_index(client, analyst, "full.md", (0.25, 0.5, 0.75, 1.0))

    premature = [
        (status, value)
        for status, value in seen
        if value >= 1.0 and status not in ("ready", "failed")
    ]
    assert not premature, (
        f"progress reported {premature} — a complete bar on a job that is still "
        "indexing"
    )


def test_a_terminal_dataset_always_ends_at_a_full_bar(
    client: TestClient, analyst: str
) -> None:
    """The other half: a finished job must not leave a bar part-filled, or the
    reader waits for something that is already over (§3.8, and §3.6's spirit)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store, "index_dataset_text", lambda **kw: 5)
        job = upload(client, analyst, [("done.md", b"# Done\n\nbody\n")])
        final = poll_status(client, analyst, job.json()["dataset_id"])
    assert final["status"] == "ready"
    assert final["progress"] == pytest.approx(1.0), final

    with pytest.MonkeyPatch.context() as mp:
        def boom(**_kwargs: Any) -> int:
            raise RuntimeError("the embedder went away")

        mp.setattr(store, "index_dataset_text", boom)
        job = upload(client, analyst, [("failed.md", b"# Failed\n\nbody\n")])
        final = poll_status(client, analyst, job.json()["dataset_id"])
    assert final["status"] == "failed"
    assert final["progress"] == pytest.approx(1.0), (
        f"a failed dataset left the bar at {final['progress']}, so it reads as still "
        "working on a job that has stopped"
    )


# --------------------------------------------------------------------------
# The mapping: the worker's slice of the bar
# --------------------------------------------------------------------------
def test_the_reporter_never_moves_the_bar_backwards() -> None:
    """The indexer reports on its own 0..1 scale and legitimately starts near
    zero. Passed through unmapped, that is a bar that jumps back to 5% after
    the redaction step — which reads as a restart that did not happen."""
    reporter = _reporter_for("ds_progress_backwards")
    recorded: list[float] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            store, "update", lambda _id, **fields: recorded.append(fields["progress"])
        )
        for fraction in (0.8, 0.1, 0.4, 0.95, 0.2):
            reporter(fraction, "message")

    assert recorded == sorted(recorded), f"the bar went backwards: {recorded}"
    assert max(recorded) <= 1.0 and min(recorded) >= 0.0, recorded


def test_the_reporter_carries_the_indexers_own_words() -> None:
    """§3.8. The message has to describe what the indexer did; a stage name
    invented by the caller is a claim about work it cannot see."""
    reporter = _reporter_for("ds_progress_words")
    recorded: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            store, "update", lambda _id, **fields: recorded.append(fields["message"])
        )
        reporter(0.5, "embedded 128 of 420 chunks")
    assert recorded == ["embedded 128 of 420 chunks"], recorded


def _reporter_for(dataset_id: str):
    factory = getattr(datasets_router, "_progress_reporter", None)
    if factory is None:  # pragma: no cover - guarded by the skip below
        pytest.skip(
            "not built yet: app/api/routers/datasets.py has no _progress_reporter, so "
            "there is no mapping from the indexer's scale onto the bar"
        )
    return factory(dataset_id)


# --------------------------------------------------------------------------
# The unit of work: one report per embedding request
# --------------------------------------------------------------------------
def _reporting_embedder():
    retrieval_datasets = pytest.importorskip(
        "retrieval.datasets", reason="needs the retrieval package (the 'real' extra)"
    )
    wrapper = getattr(retrieval_datasets, "_ReportingEmbedder", None)
    if wrapper is None:
        pytest.skip(
            "not built yet: retrieval/datasets.py has no _ReportingEmbedder, so "
            "progress during the embed cannot be reported per batch"
        )
    return wrapper


class CountingEmbedder:
    """Records every call and how many texts it was asked for."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return [[0.0, 1.0] for _ in texts]

    def health(self) -> bool:
        return True


@pytest.mark.parametrize(("count", "batch_size"), [(1, 32), (32, 32), (33, 32), (100, 8), (7, 3)])
def test_one_progress_report_per_real_embedding_request(count: int, batch_size: int) -> None:
    """The claim, at the only place it can be proved.

    One report per request the model actually served, each sent after that
    request returned. Equality is the assertion: fewer reports than requests is
    a stalled bar, and more is a number moving without work behind it.
    """
    wrapper = _reporting_embedder()
    inner = CountingEmbedder()
    reports: list[tuple[float, str]] = []
    embedder = wrapper(
        inner,
        batch_size=batch_size,
        report=lambda fraction, message: reports.append((fraction, message)),
        low=0.3,
        high=0.85,
    )

    vectors = embedder.embed_documents([f"chunk {i}" for i in range(count)])

    assert len(vectors) == count, "the wrapper dropped or duplicated chunks"
    expected_batches = math.ceil(count / batch_size)
    assert len(inner.calls) == expected_batches, (
        f"{count} chunks at batch size {batch_size} became {len(inner.calls)} requests, "
        f"expected {expected_batches}; the wrapper changed the request pattern the "
        "model sees"
    )
    assert len(reports) == len(inner.calls), (
        f"{len(inner.calls)} embedding request(s) produced {len(reports)} progress "
        "report(s); the number has to move once per unit of work, not more and not less"
    )
    fractions = [fraction for fraction, _ in reports]
    assert fractions == sorted(fractions), f"progress went backwards: {fractions}"
    assert all(0.3 <= fraction <= 0.85 for fraction in fractions), (
        f"a report left the embed's slice of the bar: {fractions}"
    )
    assert fractions[-1] == pytest.approx(0.85), (
        f"the last batch did not finish the embed's slice: {fractions[-1]}"
    )
    assert sum(inner.calls) == count


def test_the_reported_count_is_chunks_actually_embedded() -> None:
    """§3.8. The message carries a count, so the count has to be real: a
    message that says 64 when 32 have been embedded is a fabricated number in
    the one place a reader has no way to check it."""
    wrapper = _reporting_embedder()
    inner = CountingEmbedder()
    reports: list[tuple[float, str]] = []
    embedder = wrapper(
        inner,
        batch_size=10,
        report=lambda fraction, message: reports.append((fraction, message)),
        low=0.0,
        high=1.0,
    )
    embedder.embed_documents([f"chunk {i}" for i in range(25)])

    done = 0
    for index, (_, message) in enumerate(reports):
        done += inner.calls[index]
        assert f"{done:,}" in message, (
            f"report {index} says {message!r} after {done} chunk(s) were embedded"
        )
        assert "25" in message, f"report {index} does not say how many there are: {message!r}"


def test_no_work_reports_no_progress() -> None:
    """Nothing to embed is not 'embedding 0%'. An empty batch that still ticks
    the bar is the purest form of the §3.8 failure."""
    wrapper = _reporting_embedder()
    inner = CountingEmbedder()
    reports: list[Any] = []
    embedder = wrapper(
        inner, batch_size=32, report=lambda *a: reports.append(a), low=0.3, high=0.85
    )
    assert embedder.embed_documents([]) == []
    assert reports == [], f"progress was reported for no work: {reports}"
    assert inner.calls == []


def test_the_wrapper_is_otherwise_the_embedder_it_wraps() -> None:
    """The indexer must not be able to tell the difference, or the progress
    wrapper becomes a second code path that only the UI exercises."""
    wrapper = _reporting_embedder()
    inner = CountingEmbedder()
    embedder = wrapper(inner, batch_size=4, report=lambda *a: None, low=0.0, high=1.0)
    assert embedder.health() is True


def test_a_progress_callback_that_raises_does_not_fail_the_index() -> None:
    """A UI concern must not be able to mark a good index failed — that would
    turn §3.8 plumbing into a §3.6 violation."""
    retrieval_datasets = pytest.importorskip(
        "retrieval.datasets", reason="needs the retrieval package (the 'real' extra)"
    )
    guard = getattr(retrieval_datasets, "_Progress", None)
    if guard is None:
        pytest.skip("not built yet: retrieval/datasets.py has no _Progress guard")

    def explode(_fraction: float, _message: str) -> None:
        raise RuntimeError("the client went away")

    guard(explode)(0.5, "embedded 8 of 16 chunks")  # must not raise
    guard(None)(0.5, "no callback at all")
