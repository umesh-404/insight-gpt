"""Orchestration: redact -> chunk -> embed (dense + sparse) -> store.

Kept thin — every hard decision lives in the module that owns it. Documents are
processed one at a time and each is written all-or-nothing (delete-then-write),
so an interrupted run leaves a partially-updated but internally consistent
collection.

Ordering matters and is fixed here: redaction runs BEFORE chunking and embedding
(docs/04 §2.4), so a secret or a customer's card number never enters a vector or
the store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import chunk_document, embed_text
from .config import RetrievalConfig
from .embedder import Embedder
from .models import Document
from .redact import redact
from .sparse import build_sparse_vector
from .store import Store


@dataclass
class IndexStats:
    documents: int = 0
    chunks: int = 0
    redactions: int = 0
    errors: list[str] = field(default_factory=list)


class Indexer:
    def __init__(self, cfg: RetrievalConfig, store: Store, embedder: Embedder) -> None:
        self.cfg = cfg
        self.store = store
        self.embedder = embedder

    def index_documents(self, docs: list[Document]) -> IndexStats:
        stats = IndexStats()
        for doc in docs:
            try:
                stats.chunks += self._index_one(doc, stats)
                stats.documents += 1
            except Exception as exc:  # one bad document must not abort the run
                stats.errors.append(f"{doc.doc_id}: {type(exc).__name__}: {exc}")
        return stats

    def _index_one(self, doc: Document, stats: IndexStats) -> int:
        # Redact the raw text first, on a copy of the document, so nothing
        # secret reaches the chunker, the embedder, or Qdrant.
        redacted_body, n = redact(doc.body)
        redacted_title, nt = redact(doc.title)
        stats.redactions += n + nt
        doc = Document(
            **{**doc.__dict__, "body": redacted_body, "title": redacted_title}
        )

        chunks = chunk_document(doc, self.cfg.chunking)
        if not chunks:
            return 0

        texts = [embed_text(doc, c, self.cfg.chunking) for c in chunks]
        dense = self.embedder.embed_documents(texts)

        sparse = None
        if self.cfg.sparse.enabled:
            # Built from the SAME header-prefixed text that was embedded, so a
            # search keyed on an exact SKU or order id matches lexically too.
            sparse = [build_sparse_vector(t, self.cfg.sparse) for t in texts]

        return self.store.write_document(doc, chunks, dense, sparse)


def load_documents(path: Path) -> list[Document]:
    """Load documents from a JSON file (a list) or a folder of JSON files.

    Each JSON object matches the sample-document shape. A folder is read
    non-recursively, sorted by name for deterministic ordering.
    """
    raws: list[dict] = []
    if path.is_dir():
        for f in sorted(path.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            raws.extend(data if isinstance(data, list) else [data])
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        raws.extend(data if isinstance(data, list) else [data])
    return [Document.from_dict(r) for r in raws]
