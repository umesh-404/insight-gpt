"""Hybrid retrieval: dense + sparse prefetch, server-side RRF, rerank, diversity.

Two-stage design (docs/04-retrieval-rag.md §4, mirroring rememory's
``memory_mcp/search.py``): a wide cheap first stage for recall (hybrid + RRF)
and a precise expensive second stage for precision (cross-encoder rerank),
followed by per-source diversity/dedup.

Both branches build from the SAME representations used at index time: the query
is embedded with the query-side prefix and tokenized with the same sparse
builder. Any drift there would silently break matching, so it is the same
imported code, not a copy.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient, models

from .config import RetrievalConfig
from .embedder import Embedder
from .sparse import build_sparse_vector


@dataclass
class SearchHit:
    """One retrieved chunk, before it becomes a cited ``RetrievedDoc``."""

    point_id: str
    score: float  # RRF (or dense-only) score from Qdrant
    doc_id: str
    source_type: str
    title: str
    content: str
    date: str | None
    payload: dict[str, Any]
    rerank: float | None = None  # set by the reranker when it runs
    extra: dict = field(default_factory=dict)


def build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
    """Translate a plain filter dict into a Qdrant ``Filter``.

    Scalars match exactly (``MatchValue``); lists match any element
    (``MatchAny``); ``date_range`` becomes a ``created_at`` range so a recency
    word in the question is a HARD bound before ranking, not a soft hope. The
    filter is attached to BOTH prefetch branches so the candidate budget is
    spent only on in-scope documents (docs/04 §6).
    """
    if not filters:
        return None
    conditions: list[models.Condition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if key == "date_range":
            start, end = value.get("start"), value.get("end")
            if start or end:
                conditions.append(
                    models.FieldCondition(
                        key="created_at",
                        range=models.DatetimeRange(gte=start, lte=end),
                    )
                )
        elif isinstance(value, list):
            if value:
                conditions.append(
                    models.FieldCondition(key=key, match=models.MatchAny(any=value))
                )
        else:
            conditions.append(
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
            )
    return models.Filter(must=conditions) if conditions else None


class Searcher:
    def __init__(self, config: RetrievalConfig) -> None:
        self.cfg = config
        self.collection = config.collection
        self.client = QdrantClient(url=config.qdrant_url, timeout=30)
        self.embedder = Embedder(config.embedding)
        # Imported here (not at module top) to break the search <-> rerank cycle.
        from .rerank import Reranker

        self.reranker = Reranker(config)

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        k: int = 5,
        rerank: bool = True,
    ) -> list[SearchHit]:
        dense = self.embedder.embed_query(query)
        indices, values = build_sparse_vector(query, self.cfg.sparse)
        query_filter = build_filter(filters)

        mult = self.cfg.search.prefetch_multiplier
        prefetch = [
            models.Prefetch(query=dense, using="dense", filter=query_filter, limit=k * mult)
        ]
        if indices:
            prefetch.append(
                models.Prefetch(
                    query=models.SparseVector(indices=indices, values=values),
                    using="lexical",
                    filter=query_filter,
                    limit=k * mult,
                )
            )

        # Fetch enough fused candidates to feed the reranker, not just k: stage 1
        # over-fetches for recall, stage 2 restores precision.
        fetch = max(k, self.reranker.candidates if rerank else k)

        try:
            points = self.client.query_points(
                collection_name=self.collection,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=fetch,
                with_payload=True,
            ).points
        except Exception as exc:
            # The sparse branch can fail on an empty-vocabulary query (all
            # stopwords). Degrade to dense-only rather than erroring the request.
            print(f"hybrid search failed, retrying dense-only: {exc}", file=sys.stderr)
            points = self.client.query_points(
                collection_name=self.collection,
                query=dense,
                using="dense",
                query_filter=query_filter,
                limit=fetch,
                with_payload=True,
            ).points

        hits = [self._to_hit(p) for p in points]

        if rerank:
            hits = self.reranker.rerank(query, hits, pool_size=k)

        return self._diversify(hits, k)

    def _diversify(self, hits: list[SearchHit], k: int) -> list[SearchHit]:
        """Cap chunks per source document; backfill from the overflow.

        One long report (or one duplicated review) cannot monopolize the window,
        but the caller still gets k results when the corpus allows — rememory's
        diverse-then-overflow slice, keyed on ``doc_id`` (docs/04 §5).
        """
        max_per = self.cfg.search.max_per_doc
        if max_per <= 0:
            return hits[:k]
        per_doc: dict[str, int] = {}
        diverse: list[SearchHit] = []
        overflow: list[SearchHit] = []
        for h in hits:
            if per_doc.get(h.doc_id, 0) < max_per:
                per_doc[h.doc_id] = per_doc.get(h.doc_id, 0) + 1
                diverse.append(h)
            else:
                overflow.append(h)
        return (diverse + overflow)[:k]

    def _to_hit(self, point) -> SearchHit:
        p = point.payload or {}
        return SearchHit(
            point_id=str(point.id),
            score=point.score,
            doc_id=p.get("doc_id", "?"),
            source_type=p.get("source_type", "?"),
            title=p.get("title", ""),
            content=p.get("content", ""),
            date=p.get("created_at"),
            payload=p,
        )
