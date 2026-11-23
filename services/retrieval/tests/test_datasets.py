"""The indexing seam the datasets API calls, exercised without Qdrant.

``Store`` and the embedder are replaced with fakes, so these tests assert on
the contract the upload router depends on: the three calling styles, the
payload that makes a filtered delete possible, redaction running before
anything is embedded, and a failure being reported rather than swallowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrieval.config import RetrievalConfig, load_config
from retrieval.datasets import DATASET_SOURCE_TYPE, IndexResult, index_dataset
from retrieval.models import Chunk, Document


class FakeEmbedder:
    """Records everything it was asked to embed, so tests can inspect it."""

    def __init__(self, dims: int = 8) -> None:
        self.dims = dims
        self.seen: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def health(self) -> str:
        return "fake"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[0.1] * self.dims for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self.dims


class FakeStore:
    def __init__(self, cfg: RetrievalConfig) -> None:
        self.cfg = cfg
        self.collection = cfg.collection
        self.written: list[tuple[Document, list[Chunk]]] = []
        self.deleted: list[str] = []
        self.ensured = 0

    def ensure_collection(self) -> bool:
        self.ensured += 1
        return False

    def delete_document(self, doc_id: str) -> None:
        self.deleted.append(doc_id)

    def delete_dataset(self, dataset_id: str) -> int:
        self.deleted.append(dataset_id)
        return 7

    def write_document(self, doc, chunks, dense, sparse) -> int:
        self.written.append((doc, chunks))
        return len(chunks)

    def count_dataset(self, dataset_id: str) -> int:
        return sum(
            len(chunks) for doc, chunks in self.written if doc.dataset_id == dataset_id
        )


@pytest.fixture
def wired(monkeypatch):
    """``index_dataset`` with a fake store + embedder; yields both."""
    cfg = load_config()
    embedder = FakeEmbedder()
    holder: dict[str, FakeStore] = {}

    def make_store(c):
        holder["store"] = FakeStore(c)
        return holder["store"]

    monkeypatch.setattr("retrieval.datasets.Store", make_store)
    monkeypatch.setattr("retrieval.datasets.make_embedder", lambda c: embedder)
    return cfg, holder, embedder


# --------------------------------------------------------------- calling styles
def test_inline_text_is_indexed(wired):
    cfg, holder, _ = wired
    result = index_dataset("ds_abc123abc123", "notes.md", "# Q3\n\nRevenue rose in North.")
    assert isinstance(result, IndexResult)
    assert result.ok
    assert result.chunks == 1
    assert result.collection == cfg.collection
    assert holder["store"].ensured == 1


def test_a_path_positional_is_read_as_a_file(wired, tmp_path):
    f = tmp_path / "report.md"
    f.write_text("# Fulfilment\n\nThe North backlog delayed shipments.", encoding="utf-8")
    result = index_dataset("ds_abc123abc123", "report.md", f)
    assert result.ok
    doc, _ = wired[1]["store"].written[0]
    assert "North backlog" in doc.body


def test_a_string_positional_is_text_not_a_filename(wired, tmp_path):
    """A one-line CSV pasted as text must not become a file-not-found.

    The positional form is disambiguated by TYPE. If it guessed by probing the
    filesystem, a short string that happened to name a file would be read
    instead of embedded — and a string that did not would raise.
    """
    result = index_dataset("ds_abc123abc123", "inline.txt", str(tmp_path / "missing.txt"))
    assert result.ok
    doc, _ = wired[1]["store"].written[0]
    assert doc.body == str(tmp_path / "missing.txt")


def test_explicit_path_keyword_reads_a_string_path(wired, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("South apparel returns climbed.", encoding="utf-8")
    result = index_dataset("ds_abc123abc123", "a.txt", path=str(f))
    assert result.ok
    doc, _ = wired[1]["store"].written[0]
    assert "South apparel" in doc.body


def test_content_must_be_given_exactly_once(wired):
    with pytest.raises(ValueError):
        index_dataset("ds_abc123abc123", "x.txt")
    with pytest.raises(ValueError):
        index_dataset("ds_abc123abc123", "x.txt", "text", path="/tmp/x.txt")


def test_a_dataset_id_is_required(wired):
    with pytest.raises(ValueError):
        index_dataset("", "x.txt", "body")


def test_the_callers_kind_and_date_win(wired):
    """The API has already classified and timestamped the upload.

    Its answers are the ones the user is shown, so they must be the ones
    indexed: a ``kind`` inferred here that disagreed with the listing would be
    a second, invisible source of truth.
    """
    result = index_dataset(
        dataset_id="ds_abc123abc123",
        name="mystery",
        kind="tabular",
        text="a,b\n1,2",
        created_at="2026-04-02",
    )
    assert result.ok
    assert result.kind == "tabular"
    doc, _ = wired[1]["store"].written[0]
    assert doc.date == "2026-04-02"


# --------------------------------------------------------------------- payload
def test_every_point_carries_dataset_id_and_name(wired):
    """Deletion keys on ``dataset_id``; a citation names ``dataset_name``.

    Without these on the document, forgetting one upload would need a
    collection drop — which the whole store refuses — and an answer could cite
    an uploaded chunk without saying which upload it came from.
    """
    index_dataset("ds_feedfeedfeed", "q3_sales.csv", "North electronics revenue fell 12%.")
    doc, _ = wired[1]["store"].written[0]
    assert doc.dataset_id == "ds_feedfeedfeed"
    assert doc.dataset_name == "q3_sales.csv"
    # doc_id == dataset_id, so a re-index of the same dataset overwrites its
    # own chunks in place (retrieval.ids derives point ids from it) rather than
    # accumulating a second copy beside the first.
    assert doc.doc_id == "ds_feedfeedfeed"


def test_the_citation_title_is_the_dataset_name(wired):
    index_dataset("ds_feedfeedfeed", "customer_complaints.csv", "late delivery again")
    doc, _ = wired[1]["store"].written[0]
    assert doc.title == "customer_complaints.csv"


def test_uploads_are_a_distinct_source_type(wired):
    """An uploaded chunk must be distinguishable from the curated corpus."""
    index_dataset("ds_feedfeedfeed", "mine.txt", "my own numbers")
    doc, _ = wired[1]["store"].written[0]
    assert doc.source_type == DATASET_SOURCE_TYPE
    assert doc.source_type not in ("ticket", "review", "report", "email")


# -------------------------------------------------------------------- redaction
def test_redaction_runs_before_anything_is_embedded(wired):
    """Contract §6.4: secrets never reach Qdrant, on this path too."""
    _, holder, embedder = wired
    body = (
        "Escalation notes\n\n"
        "Customer wrote from alice@example.com about order ORD-88213.\n"
        'The integration uses api_key = "sk_live_3f9a8b7c6d5e4f3a2b1c".\n'
        "Card on file 4111 1111 1111 1111.\n"
    )
    result = index_dataset("ds_abc123abc123", "escalation.txt", body)

    assert result.redactions >= 3
    doc, _ = holder["store"].written[0]
    embedded = "\n".join(embedder.seen)
    for secret in ("alice@example.com", "4111 1111 1111 1111",
                   "sk_live_3f9a8b7c6d5e4f3a2b1c"):
        assert secret not in doc.body
        assert secret not in embedded
    # The order reference is business data, not PII, and must survive.
    assert "ORD-88213" in doc.body


# ---------------------------------------------------------------------- failure
def test_empty_text_is_a_failure_not_an_empty_success(wired):
    """Contract §6.6: a dataset that indexed nothing must not look ready."""
    result = index_dataset("ds_abc123abc123", "blank.txt", "   \n  \n")
    assert not result.ok
    assert result.error
    assert result.chunks == 0


def test_an_unsupported_file_is_a_permanent_error_naming_what_is_accepted(wired, tmp_path):
    f = tmp_path / "thing.exe"
    f.write_bytes(b"MZ")
    result = index_dataset("ds_abc123abc123", "thing.exe", f)
    assert not result.ok
    assert ".csv" in result.error and ".pdf" in result.error


def test_an_embedder_failure_is_reported_not_raised(monkeypatch, wired):
    """The background job must END, with a reason, rather than hang at 'indexing'."""
    def broken(cfg):
        raise RuntimeError("Ollama unreachable at http://127.0.0.1:11434")

    monkeypatch.setattr("retrieval.datasets.make_embedder", broken)
    result = index_dataset("ds_abc123abc123", "x.txt", "some text")
    assert not result.ok
    assert "Ollama unreachable" in result.error


def test_zero_chunks_written_is_not_ok(monkeypatch, wired):
    monkeypatch.setattr(FakeStore, "count_dataset", lambda self, d: 0)
    result = index_dataset("ds_abc123abc123", "x.txt", "some text")
    assert not result.ok
    assert "searchable" in result.error


# --------------------------------------------------------------------- progress
def test_progress_is_reported_and_a_broken_callback_cannot_fail_the_index(wired):
    seen: list[tuple[float, str]] = []
    result = index_dataset(
        "ds_abc123abc123", "a.txt", "text", progress=lambda f, m: seen.append((f, m))
    )
    assert result.ok
    assert seen and seen[0][0] < seen[-1][0]
    assert seen[-1][0] == 1.0

    def explode(fraction, message):
        raise RuntimeError("the UI blew up")

    assert index_dataset("ds_abc123abc123", "a.txt", "text", progress=explode).ok


# ----------------------------------------------------------------------- delete
def test_delete_dataset_delegates_to_a_filtered_delete(monkeypatch):
    from retrieval import datasets as mod

    recorded: list[str] = []

    class Recording:
        def __init__(self, cfg):
            pass

        def delete_dataset(self, dataset_id):
            recorded.append(dataset_id)
            return 12

    monkeypatch.setattr(mod, "Store", Recording)
    assert mod.delete_dataset("ds_abc123abc123") == 12
    assert recorded == ["ds_abc123abc123"]

    with pytest.raises(ValueError):
        mod.delete_dataset("")


def test_a_path_object_and_a_str_resolve_differently():
    from retrieval.datasets import _resolve_source

    assert _resolve_source(Path("/tmp/a.csv"), text=None, path=None) == (
        None, Path("/tmp/a.csv")
    )
    assert _resolve_source("a,b\n1,2", text=None, path=None) == ("a,b\n1,2", None)


def test_progress_is_reported_once_per_embedding_batch(wired, monkeypatch):
    """The embed is the slow step, so it is the step that has to report.

    Progress used to jump straight from "embedding" to "done", which on a real
    file is a bar that sits still for the minutes that actually matter. The
    unit is one report per request sent to the embedding model - real work, not
    a timer (contract 3.8) - and the request pattern is unchanged: the batches
    are cut on the same configured `batch_size` the embedder uses internally.
    """
    cfg, holder, embedder = wired
    monkeypatch.setattr(cfg.embedding, "batch_size", 2)
    # Long enough to chunk into several pieces, so there is more than one batch.
    body = "\n\n".join(f"Paragraph {i}. " + ("sales figures " * 60) for i in range(12))

    seen: list[tuple[float, str]] = []
    result = index_dataset(
        "ds_abc123abc123",
        "a.txt",
        body,
        config=cfg,
        progress=lambda f, m: seen.append((f, m)),
    )
    assert result.ok

    batch_reports = [(f, m) for f, m in seen if m.startswith("embedded ")]
    assert len(batch_reports) >= 2, f"expected a report per batch, got {seen}"
    # One request per batch of `batch_size` texts.
    calls = (len(embedder.seen) + 1) // 2
    assert len(batch_reports) == calls
    # Monotone, inside the embed's own slice of the bar, and never pretending
    # the write has happened while chunks are still being embedded.
    fractions = [f for f, _ in batch_reports]
    assert fractions == sorted(fractions)
    assert all(0.3 <= f <= 0.85 for f in fractions)
    # The message counts chunks that are done, not chunks that were started.
    assert batch_reports[-1][1].endswith(f"of {len(embedder.seen):,} chunks")
