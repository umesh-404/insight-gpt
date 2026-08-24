"""InsightGPT retrieval / RAG service.

Hybrid dense + sparse retrieval over a single Qdrant ``documents`` collection,
server-side RRF fusion, cross-encoder reranking, and per-source diversity —
the pipeline described in ``docs/04-retrieval-rag.md``, reusing rememory's
retrieval-core patterns.

The public entry point is :class:`QdrantRetriever`, which conforms to the insight
engine's ``search(query, *, filters, k) -> list[RetrievedDoc]`` interface.
"""

from __future__ import annotations

from .config import RetrievalConfig, get_config, load_config
from .corpus import IndexState, default_corpus_path, load_corpus, state_path_for
from .models import Chunk, Document, RetrievedDoc
from .retriever import QdrantRetriever
from .schema import AUTHOR_ROLES, SOURCE_TYPES, content_hash, normalize_document

__all__ = [
    "AUTHOR_ROLES",
    "SOURCE_TYPES",
    "Chunk",
    "Document",
    "IndexState",
    "QdrantRetriever",
    "RetrievalConfig",
    "RetrievedDoc",
    "content_hash",
    "default_corpus_path",
    "get_config",
    "load_config",
    "load_corpus",
    "normalize_document",
    "state_path_for",
]
