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

from dataclasses import dataclass, field
from pathlib import Path

from .chunking import chunk_document, embed_text
from .config import RetrievalConfig
from .corpus import IndexState, load_corpus
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
    skipped_unchanged: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.documents} documents, {self.chunks} chunks, "
            f"{self.redactions} redactions, {self.skipped_unchanged} unchanged, "
            f"{self.removed} removed"
        )


class Indexer:
    def __init__(self, cfg: RetrievalConfig, store: Store, embedder: Embedder) -> None:
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self._chat = None  # lazy OllamaChat for optional contextual augmentation

    def _llm_context(self, doc: Document) -> str | None:
        """One-line situating context for a document, if ``contextual_llm`` is on.

        Best-effort and offline-safe: returns ``None`` when the flag is off or
        Ollama is unavailable. The result is embedded only (never stored, never
        hashed), so a non-deterministic sentence cannot churn changed-only
        indexing — an unchanged document is skipped before this is ever called.
        """
        if not self.cfg.chunking.contextual_llm:
            return None
        if self._chat is None:
            from .chat import OllamaChat

            model = self.cfg.chunking.context_model or self.cfg.query_rewrite.model
            self._chat = OllamaChat(self.cfg.embedding.base_url, model)
        if not self._chat.usable:
            return None
        prompt = (
            f"Document title: {doc.title}\n"
            f"Type: {doc.source_type}; region: {doc.region or '-'}; "
            f"category: {doc.category or '-'}\n\n{doc.body[:1500]}\n\n"
            "In one short sentence, situate this document for search."
        )
        return self._chat.complete(prompt, num_predict=64)

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

        # Optional LLM situating context, computed ONCE per document (not per
        # chunk). Best-effort: None when disabled or Ollama is unavailable, in
        # which case the deterministic breadcrumb augmentation still applies.
        doc_context = self._llm_context(doc)
        texts = [embed_text(doc, c, self.cfg.chunking, doc_context=doc_context) for c in chunks]
        dense = self.embedder.embed_documents(texts)

        sparse = None
        if self.cfg.sparse.enabled:
            # Built from the SAME header-prefixed text that was embedded, so a
            # search keyed on an exact SKU or order id matches lexically too.
            sparse = [build_sparse_vector(t, self.cfg.sparse) for t in texts]

        return self.store.write_document(doc, chunks, dense, sparse)

    # ---------------------------------------------------------------- changed-only
    def index_changed(
        self, docs: list[Document], state: IndexState, *, full: bool = False
    ) -> IndexStats:
        """Re-index only what changed since the last recorded run.

        Embedding is the expensive step, so an unchanged document must cost
        nothing. Documents that vanished from the corpus have their chunks
        deleted, otherwise a retracted ticket would stay retrievable forever.

        The state file is written only for work that actually succeeded: a
        document that errored is left out, so the next run retries it.
        """
        plan = state.plan(docs, full=full)
        stats = IndexStats(skipped_unchanged=plan.unchanged)

        for doc_id in plan.removed:
            try:
                self.store.delete_document(doc_id)
            except Exception as exc:  # noqa: BLE001 — report, keep going
                stats.errors.append(f"{doc_id}: delete failed: {type(exc).__name__}: {exc}")
            else:
                stats.removed += 1
                state.forget([doc_id])

        indexed: list[Document] = []
        for doc in plan.to_index:
            try:
                stats.chunks += self._index_one(doc, stats)
            except Exception as exc:  # noqa: BLE001 — one bad doc must not abort
                stats.errors.append(f"{doc.doc_id}: {type(exc).__name__}: {exc}")
            else:
                stats.documents += 1
                indexed.append(doc)

        state.record(indexed)
        state.save()
        return stats


def load_documents(path: Path) -> list[Document]:
    """Load documents from a JSON file (a list) or a folder of JSON files.

    Thin alias for :func:`retrieval.corpus.load_corpus`, kept because it is the
    name callers (worker jobs, the CLI) already import.
    """
    return load_corpus(path)
