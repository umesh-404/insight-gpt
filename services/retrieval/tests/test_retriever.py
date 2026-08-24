"""Offline tests for the engine-facing shape and per-source diversity.

These avoid any network: the retriever's mapping and the diversity slice are
pure given a list of hits, so both are constructed with ``__new__`` and fed
synthetic ``SearchHit``s — no Qdrant or Ollama.
"""

from __future__ import annotations

from retrieval.config import RetrievalConfig
from retrieval.models import RetrievedDoc
from retrieval.retriever import QdrantRetriever
from retrieval.search import Searcher, SearchHit


def _hit(doc_id: str, score: float, rerank: float | None = None, **payload) -> SearchHit:
    return SearchHit(
        point_id=f"{doc_id}-0",
        score=score,
        doc_id=doc_id,
        source_type=payload.get("source_type", "ticket"),
        title=payload.get("title", f"title {doc_id}"),
        content=payload.get("content", f"body {doc_id}"),
        date=payload.get("created_at"),
        payload=payload,
        rerank=rerank,
    )


class _FakeSearcher:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    def search(self, query, *, filters=None, k=5, rerank=True):
        return self._hits[:k]


def test_retriever_returns_engine_compatible_shape():
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.cfg = RetrievalConfig()
    retriever.searcher = _FakeSearcher(
        [
            _hit("TICKET-1", 0.5, rerank=0.91, region="North", category="Electronics",
                 author_role="agent", created_at="2026-05-08"),
        ]
    )
    results = retriever.search("late deliveries", k=5)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, RetrievedDoc)
    # Exact field set the engine's RetrievedDoc declares.
    assert set(r.model_dump()) == {
        "doc_id", "source_type", "title", "body", "date", "score", "metadata"
    }
    assert r.doc_id == "TICKET-1"
    assert r.date == "2026-05-08"
    # Rerank score wins over the RRF score when present.
    assert r.score == 0.91
    assert r.metadata["region"] == "North"
    assert r.metadata["author_role"] == "agent"


def test_retriever_falls_back_to_rrf_score_without_rerank():
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.cfg = RetrievalConfig()
    retriever.searcher = _FakeSearcher([_hit("R-1", 0.42)])
    assert retriever.search("q")[0].score == 0.42


def test_diversity_caps_chunks_per_document():
    searcher = Searcher.__new__(Searcher)
    cfg = RetrievalConfig()
    cfg.search.max_per_doc = 2
    searcher.cfg = cfg
    # One noisy document contributes 4 chunks; two others one each.
    hits = (
        [_hit("LOUD", 0.9) for _ in range(4)]
        + [_hit("B", 0.5), _hit("C", 0.4)]
    )
    # give the LOUD hits distinct point ids so they are genuinely separate rows
    for i, h in enumerate(hits[:4]):
        h.point_id = f"LOUD-{i}"
    out = searcher._diversify(hits, k=4)
    assert sum(1 for h in out if h.doc_id == "LOUD") == 2  # capped
    assert {h.doc_id for h in out} == {"LOUD", "B", "C"}
    assert len(out) == 4  # backfilled from overflow to still return k


def test_diversity_disabled_when_max_per_doc_zero():
    searcher = Searcher.__new__(Searcher)
    cfg = RetrievalConfig()
    cfg.search.max_per_doc = 0
    searcher.cfg = cfg
    hits = [_hit("A", 0.9), _hit("A", 0.8), _hit("A", 0.7)]
    for i, h in enumerate(hits):
        h.point_id = f"A-{i}"
    assert len(searcher._diversify(hits, k=2)) == 2
