"""Shared data shapes for the retrieval service.

``RetrievedDoc`` is the load-bearing one: it is the exact shape the insight
engine consumes (``services/api/app/engine/retrieval.py``), so ``QdrantRetriever``
can be swapped in for ``FixtureRetriever`` via config with no engine change.
Keep its fields — ``doc_id, source_type, title, body, date, score, metadata`` —
in lockstep with the engine's ``RetrievedDoc``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class RetrievedDoc(BaseModel):
    """A single cited result. Interface-compatible with the engine's model."""

    doc_id: str
    source_type: str
    title: str
    body: str
    date: str | None = None
    score: float = 0.0
    metadata: dict = {}


@dataclass
class Document:
    """One source document to index (a ticket, review, report, or email).

    Mirrors the shape of ``services/api/app/fixtures/retail.py::get_sample_documents``
    so the same JSON drives both the fixture retriever and this real one.
    """

    doc_id: str
    source_type: str  # ticket | review | report | email
    title: str
    body: str
    date: str | None = None  # ISO-8601; stored as `created_at` in the payload
    region: str | None = None
    category: str | None = None
    product_ref: str | None = None
    order_ref: str | None = None
    author_role: str | None = None
    channel: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Document:
        return cls(
            doc_id=raw["doc_id"],
            source_type=raw["source_type"],
            title=raw.get("title", ""),
            body=raw.get("body", ""),
            date=raw.get("date") or raw.get("created_at"),
            region=raw.get("region"),
            category=raw.get("category"),
            product_ref=raw.get("product_ref"),
            order_ref=raw.get("order_ref"),
            author_role=raw.get("author_role"),
            channel=raw.get("channel"),
        )


@dataclass
class Chunk:
    """One embeddable unit of a document, plus its provenance.

    ``content`` is the verbatim text quoted back to the user. The breadcrumb
    header (see ``chunking.embed_text``) is prepended only to the embedded and
    sparse text, never stored here.
    """

    content: str
    chunk_index: int
    chunk_total: int
    heading_path: str | None = None  # reports: "Fulfilment > Root cause"
    extra: dict = field(default_factory=dict)
