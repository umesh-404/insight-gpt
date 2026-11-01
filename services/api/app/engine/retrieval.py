"""Retrieval client the engine calls for document context.

``Retriever`` is the interface. The real implementation (Qdrant hybrid search +
cross-encoder rerank, per ``docs/04-retrieval-rag.md``) lands in the retrieval
phase. ``FixtureRetriever`` does simple keyword scoring over the sample documents
so the engine's unstructured/hybrid paths run end-to-end now. The engine does no
retrieval logic of its own — it just consumes ranked, cited results.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from typing import Protocol

from pydantic import BaseModel

log = logging.getLogger("insightgpt.retrieval")

_STOP = {"the", "a", "an", "did", "in", "on", "of", "to", "and", "why", "what",
         "this", "that", "last", "month", "quarter", "for", "is", "are", "our",
         "we", "customers", "customer"}


def _load_default_corpus() -> list[dict]:
    """Load sample documents and any generated documents found in data/generated/documents."""
    from ..fixtures.retail import get_sample_documents
    docs: list[dict] = list(get_sample_documents())

    # Look for generated/documents directory
    candidates = [
        Path(os.environ.get("DOCUMENT_CORPUS_PATH", "")),
        Path("data/generated/documents"),
        Path(__file__).resolve().parents[4] / "data" / "generated" / "documents",
        Path(__file__).resolve().parents[3] / "data" / "generated" / "documents",
    ]

    for cand in candidates:
        if cand and cand.is_dir():
            for json_file in cand.glob("*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and "doc_id" in item:
                                docs.append({
                                    "doc_id": item["doc_id"],
                                    "source_type": item.get("doc_type") or item.get("source_type") or "document",
                                    "title": item.get("title") or item["doc_id"],
                                    "body": item.get("body") or "",
                                    "date": item.get("created_ts", "")[:10] if item.get("created_ts") else item.get("date"),
                                    "region": item.get("region"),
                                    "category": item.get("category"),
                                    "author_role": item.get("author_role"),
                                })
                except Exception as exc:
                    log.warning("Could not read document file %s: %s", json_file, exc)
            break

    return docs


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
    """Keyword-overlap ranking over sample documents and generated documents corpus."""

    def __init__(self, documents: list[dict] | None = None):
        if documents is None:
            documents = _load_default_corpus()
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
