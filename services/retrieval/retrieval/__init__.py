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
from .models import Chunk, Document, RetrievedDoc
from .retriever import QdrantRetriever

__all__ = [
    "Chunk",
    "Document",
    "QdrantRetriever",
    "RetrievalConfig",
    "RetrievedDoc",
    "get_config",
    "load_config",
]
