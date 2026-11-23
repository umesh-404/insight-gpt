"""InsightGPT retrieval / RAG service.

Hybrid dense + sparse retrieval over a single Qdrant collection
(``insightgpt_documents`` by default), server-side RRF fusion, cross-encoder
reranking, and per-source diversity — the pipeline described in
``docs/04-retrieval-rag.md``, reusing rememory's retrieval-core patterns.

Two public entry points:

* :class:`QdrantRetriever` — query side. Conforms to the insight engine's
  ``search(query, *, filters, k) -> list[RetrievedDoc]`` interface.
* :func:`index_dataset` / :func:`delete_dataset` (:mod:`retrieval.datasets`) —
  write side, for the datasets upload API.
"""

from __future__ import annotations

from .config import RetrievalConfig, get_config, load_config
from .corpus import IndexState, default_corpus_path, load_corpus, state_path_for
from .datasets import IndexResult, dataset_chunk_count, delete_dataset, index_dataset
from .loaders import SUPPORTED_EXTENSIONS, UnsupportedDatasetFile, classify
from .models import Chunk, Document, RetrievedDoc
from .retriever import QdrantRetriever
from .rewrite import QueryRewriter, RewrittenQuery
from .schema import AUTHOR_ROLES, SOURCE_TYPES, content_hash, normalize_document

__all__ = [
    "AUTHOR_ROLES",
    "SOURCE_TYPES",
    "SUPPORTED_EXTENSIONS",
    "Chunk",
    "Document",
    "IndexResult",
    "IndexState",
    "QdrantRetriever",
    "QueryRewriter",
    "RetrievalConfig",
    "RetrievedDoc",
    "RewrittenQuery",
    "UnsupportedDatasetFile",
    "classify",
    "content_hash",
    "dataset_chunk_count",
    "default_corpus_path",
    "delete_dataset",
    "get_config",
    "index_dataset",
    "load_config",
    "load_corpus",
    "normalize_document",
    "state_path_for",
]
