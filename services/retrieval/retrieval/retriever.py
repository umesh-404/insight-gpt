"""``QdrantRetriever`` — the engine-facing entry point.

Implements the exact ``search(query, *, filters, k) -> list[RetrievedDoc]`` shape
the insight engine consumes (``services/api/app/engine/retrieval.py``), so the
engine can swap ``FixtureRetriever`` for this one via config with no code change.
This class is the only thing the engine needs to know about; everything else
(embedder, store, hybrid search, rerank) sits behind it.

The ``score`` returned is the reranker's answerhood probability when reranking
ran, otherwise the RRF score — either way a monotonic relevance signal the
engine and UI can rank and display.
"""

from __future__ import annotations

from typing import Any

from .config import RetrievalConfig, get_config
from .models import RetrievedDoc
from .search import Searcher


class QdrantRetriever:
    """Hybrid Qdrant retrieval behind the engine's ``Retriever`` protocol."""

    # This retriever reads the live collection, so a document the user uploaded
    # through the datasets API is genuinely retrievable here. The API's upload
    # path checks this (via ``app.engine.build.datasets_are_searchable``) before
    # accepting a file, so it never indexes an upload that nothing will search.
    searches_uploaded_datasets = True

    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.cfg = config or get_config()
        self.searcher = Searcher(self.cfg)

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        k: int = 5,
    ) -> list[RetrievedDoc]:
        hits = self.searcher.search(query, filters=filters, k=k, rerank=True)
        out: list[RetrievedDoc] = []
        for h in hits:
            score = h.rerank if h.rerank is not None else h.score
            out.append(
                RetrievedDoc(
                    doc_id=h.doc_id,
                    source_type=h.source_type,
                    title=h.title,
                    body=h.content,
                    date=h.date,
                    score=round(float(score), 4),
                    metadata={
                        key: h.payload.get(key)
                        for key in ("region", "category", "product_ref",
                                    "order_ref", "author_role", "channel", "heading_path",
                                    # Provenance for an uploaded dataset: which
                                    # dataset this chunk came from, by id and by
                                    # name. The citation's title is already the
                                    # dataset's name (see retrieval.datasets), so
                                    # an answer can never cite an upload without
                                    # naming it (contract §6.2).
                                    "dataset_id", "dataset_name")
                        if h.payload.get(key) is not None
                    },
                )
            )
        return out
