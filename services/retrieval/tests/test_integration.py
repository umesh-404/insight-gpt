"""Live end-to-end test — skipped unless Qdrant AND Ollama are reachable.

Gated so the offline suite passes with no services running. Enable by exporting
``RETRIEVAL_LIVE=1`` (and having Qdrant + Ollama up with the configured models
pulled). It indexes the sample corpus and asserts the planted North-electronics
story is retrievable above the South-apparel negative control.
"""

from __future__ import annotations

import os

import httpx
import pytest

from retrieval.config import get_config


def _service_up(url: str) -> bool:
    try:
        httpx.get(url, timeout=2).raise_for_status()
        return True
    except (httpx.HTTPError, OSError):
        return False


def _live_enabled() -> bool:
    if os.environ.get("RETRIEVAL_LIVE") != "1":
        return False
    cfg = get_config()
    return _service_up(f"{cfg.qdrant_url}/readyz") and _service_up(
        f"{cfg.embedding.base_url}/api/tags"
    )


pytestmark = pytest.mark.skipif(
    not _live_enabled(),
    reason="live Qdrant + Ollama not available (set RETRIEVAL_LIVE=1 to enable)",
)


def test_index_and_search_roundtrip():
    from retrieval.embedder import Embedder
    from retrieval.indexer import Indexer
    from retrieval.models import Document
    from retrieval.retriever import QdrantRetriever
    from retrieval.sample_docs import get_sample_documents
    from retrieval.store import Store

    cfg = get_config()
    store = Store(cfg)
    store.ensure_collection()
    docs = [Document.from_dict(d) for d in get_sample_documents()]
    with Embedder(cfg.embedding) as embedder:
        embedder.health()
        stats = Indexer(cfg, store, embedder).index_documents(docs)
    assert stats.documents == len(docs)
    assert not stats.errors

    results = QdrantRetriever(cfg).search("why are North electronics deliveries late?", k=5)
    top_ids = [r.doc_id for r in results]
    assert any(d in top_ids for d in ("TICKET-40122", "TICKET-40210", "REPORT-Q2-OPS"))
    # The negative control must not be the top hit.
    assert top_ids[0] != "REVIEW-9950"
