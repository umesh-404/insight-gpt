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

from .schema import CONTENT_HASH_KEY, normalize_document


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
    author_role: str | None = None  # customer | agent | manager
    channel: str | None = None
    # Hash of the index-relevant content, used for changed-only re-indexing.
    content_hash: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Document:
        """Build from ANY producer's spelling via :mod:`retrieval.schema`.

        The ingestion corpus uses ``doc_type`` / ``created_ts`` /
        ``author_role='support_agent'``; the built-in samples use
        ``source_type`` / ``date`` / ``author_role='agent'``. Both land here as
        the same canonical document, so the payload the engine filters on is
        identical whichever produced it.
        """
        c = normalize_document(raw)
        return cls(
            doc_id=c["doc_id"],
            source_type=c["source_type"],
            title=c["title"],
            body=c["body"],
            date=c["created_at"],
            region=c["region"],
            category=c["category"],
            product_ref=c["product_ref"],
            order_ref=c["order_ref"],
            author_role=c["author_role"],
            channel=c["channel"],
            content_hash=c[CONTENT_HASH_KEY],
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
