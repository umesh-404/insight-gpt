"""``Indexer.index_changed`` — the part of the hand-off that has to be cheap.

Store and embedder are fakes: what is under test is the orchestration (what gets
embedded, what gets deleted, what the state file remembers), not Qdrant or
Ollama. Those are covered by ``test_integration.py``, which is gated on live
services.
"""

from __future__ import annotations

import json

import pytest

from retrieval.config import RetrievalConfig
from retrieval.corpus import STATE_FILENAME, IndexState, load_corpus
from retrieval.indexer import Indexer

DOCS = [
    {
        "doc_id": "TICKET-1",
        "doc_type": "ticket",
        "title": "Late delivery",
        "body": "North fulfilment backlog delayed the order.",
        "created_ts": "2026-05-08T09:00:00",
        "region": "North",
        "category": "Electronics",
        "author_role": "support_agent",
    },
    {
        "doc_id": "REVIEW-1",
        "doc_type": "review",
        "title": "Great",
        "body": "Fast shipping in the South, no complaints at all.",
        "created_ts": "2026-05-11T12:00:00",
        "region": "South",
        "category": "Apparel",
        "author_role": "customer",
    },
]


class FakeStore:
    """Records what the indexer asked it to write and delete."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self.deleted: list[str] = []

    def delete_document(self, doc_id: str) -> None:
        self.deleted.append(doc_id)

    def write_document(self, doc, chunks, dense, sparse) -> int:
        self.written.append(doc.doc_id)
        return len(chunks)


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        return [[0.0] * 8 for _ in texts]


@pytest.fixture
def cfg() -> RetrievalConfig:
    return RetrievalConfig()


@pytest.fixture
def corpus_file(tmp_path):
    path = tmp_path / "documents.json"
    path.write_text(json.dumps(DOCS), encoding="utf-8")
    return path


def _run(cfg, corpus_file, tmp_path, *, full=False):
    store, embedder = FakeStore(), FakeEmbedder()
    state = IndexState(tmp_path / STATE_FILENAME, cfg.collection)
    stats = Indexer(cfg, store, embedder).index_changed(
        load_corpus(corpus_file), state, full=full
    )
    return stats, store, embedder


def test_first_run_indexes_the_whole_corpus(cfg, corpus_file, tmp_path):
    stats, store, embedder = _run(cfg, corpus_file, tmp_path)
    assert stats.documents == 2
    assert stats.chunks > 0
    assert stats.skipped_unchanged == 0
    assert sorted(store.written) == ["REVIEW-1", "TICKET-1"]
    assert embedder.calls > 0


def test_re_running_an_unchanged_corpus_embeds_nothing(cfg, corpus_file, tmp_path):
    _run(cfg, corpus_file, tmp_path)
    stats, store, embedder = _run(cfg, corpus_file, tmp_path)

    assert stats.documents == 0
    assert stats.skipped_unchanged == 2
    assert store.written == []
    assert embedder.calls == 0  # the whole point: no embedding cost


def test_only_the_changed_document_is_re_embedded(cfg, corpus_file, tmp_path):
    _run(cfg, corpus_file, tmp_path)
    edited = [dict(DOCS[0], body="North backlog. Escalated to operations."), DOCS[1]]
    corpus_file.write_text(json.dumps(edited), encoding="utf-8")

    stats, store, _ = _run(cfg, corpus_file, tmp_path)
    assert store.written == ["TICKET-1"]
    assert stats.skipped_unchanged == 1


def test_full_run_re_embeds_everything(cfg, corpus_file, tmp_path):
    _run(cfg, corpus_file, tmp_path)
    stats, store, _ = _run(cfg, corpus_file, tmp_path, full=True)
    assert sorted(store.written) == ["REVIEW-1", "TICKET-1"]
    assert stats.skipped_unchanged == 0


def test_document_dropped_from_the_corpus_is_deleted_from_the_store(
    cfg, corpus_file, tmp_path
):
    _run(cfg, corpus_file, tmp_path)
    corpus_file.write_text(json.dumps([DOCS[1]]), encoding="utf-8")

    stats, store, _ = _run(cfg, corpus_file, tmp_path)
    assert store.deleted == ["TICKET-1"]
    assert stats.removed == 1

    # ...and it is not re-deleted on the run after that.
    _, store2, _ = _run(cfg, corpus_file, tmp_path)
    assert store2.deleted == []


def test_a_failed_document_is_retried_next_run(cfg, corpus_file, tmp_path):
    """State records only what succeeded, so a failure is not silently forgotten."""

    class HalfBrokenStore(FakeStore):
        def write_document(self, doc, chunks, dense, sparse):
            if doc.doc_id == "TICKET-1":
                raise RuntimeError("qdrant went away")
            return super().write_document(doc, chunks, dense, sparse)

    state = IndexState(tmp_path / STATE_FILENAME, cfg.collection)
    stats = Indexer(cfg, HalfBrokenStore(), FakeEmbedder()).index_changed(
        load_corpus(corpus_file), state
    )
    assert stats.documents == 1
    assert len(stats.errors) == 1
    assert "TICKET-1" in stats.errors[0]

    _, store, _ = _run(cfg, corpus_file, tmp_path)
    assert store.written == ["TICKET-1"]


def test_summary_reports_every_counter(cfg, corpus_file, tmp_path):
    stats, _, _ = _run(cfg, corpus_file, tmp_path)
    summary = stats.summary()
    for word in ("documents", "chunks", "redactions", "unchanged", "removed"):
        assert word in summary
