"""Retrieval client the engine calls for document context.

``Retriever`` is the interface. The real implementation (Qdrant hybrid search +
cross-encoder rerank, per ``docs/04-retrieval-rag.md``) lands in the retrieval
phase. ``FixtureRetriever`` does simple keyword scoring over the sample documents
so the engine's unstructured/hybrid paths run end-to-end now. The engine does no
retrieval logic of its own — it just consumes ranked, cited results.
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel

_STOP = {"the", "a", "an", "did", "in", "on", "of", "to", "and", "why", "what",
         "this", "that", "last", "month", "quarter", "for", "is", "are", "our",
         "we", "customers", "customer"}


class RetrievedDoc(BaseModel):
    doc_id: str
    source_type: str
    title: str
    body: str
    date: str | None = None
    score: float = 0.0
    metadata: dict = {}


class Retriever(Protocol):
    def search(self, query: str, *, filters: dict | None = None, k: int = 5) -> list[RetrievedDoc]:
        ...


class FixtureRetriever:
    """Keyword-overlap ranking over the in-repo sample documents."""

    def __init__(self, documents: list[dict] | None = None):
        if documents is None:
            from ..fixtures.retail import get_sample_documents
            documents = get_sample_documents()
        self._docs = documents

    def search(self, query: str, *, filters: dict | None = None, k: int = 5) -> list[RetrievedDoc]:
        filters = filters or {}
        terms = _tokenize(query)
        # Entity terms from filters strengthen matching (region/category scoping).
        for key in ("region", "category"):
            for v in filters.get(key, []) or []:
                terms.add(v.lower())

        scored: list[tuple[float, dict]] = []
        for doc in self._docs:
            haystack = _tokenize(f"{doc['title']} {doc['body']} "
                                 f"{doc.get('region','')} {doc.get('category','')}")
            overlap = len(terms & haystack)
            if overlap == 0:
                continue
            score = overlap / (len(terms) or 1)
            # Soft date preference: nudge, never exclude (fixture data is sparse).
            score += _date_bonus(doc.get("date"), filters.get("date_range"))
            scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, doc in scored[:k]:
            results.append(RetrievedDoc(
                doc_id=doc["doc_id"], source_type=doc["source_type"],
                title=doc["title"], body=doc["body"], date=doc.get("date"),
                score=round(min(score, 0.99), 3),
                metadata={k2: doc.get(k2) for k2 in ("region", "category", "author_role")},
            ))
        return results


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def _date_bonus(doc_date: str | None, date_range: dict | None) -> float:
    if not doc_date or not date_range:
        return 0.0
    start, end = date_range.get("start"), date_range.get("end")
    if start and end and start <= doc_date <= end:
        return 0.15
    return 0.0
