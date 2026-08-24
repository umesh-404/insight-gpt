"""Typed configuration for the retrieval service.

Everything downstream reads config through here, so there is exactly one place
that knows the file layout. Loading is pydantic-validated and fails loudly: a
config error that surfaces halfway through a long index is far more expensive
than one that surfaces immediately.

Hosts default to the YAML value but can be overridden by environment variables
(``OLLAMA_HOST``, ``QDRANT_URL``) so a container can point at sibling services
without editing the file — mirroring how ``services/api`` reads its settings.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "retrieval.yaml"


class EmbeddingConfig(BaseModel):
    base_url: str = "http://127.0.0.1:11434"
    model: str = "nomic-embed-text"
    dimensions: int = 768
    distance: str = "Cosine"
    max_context_tokens: int = 2048
    batch_size: int = 32
    keep_alive: str = "30m"
    timeout_seconds: float = 120.0
    query_prefix: str = "search_query: "
    document_prefix: str = "search_document: "


class SparseConfig(BaseModel):
    enabled: bool = True
    split_identifiers: bool = True
    min_token_len: int = 2
    max_tokens_per_chunk: int = 400


class ChunkingConfig(BaseModel):
    max_chunk_chars: int = 1600
    min_chunk_chars: int = 200
    overlap_lines: int = 2
    contextual_header: bool = True


class SearchConfig(BaseModel):
    prefetch_multiplier: int = 4
    max_per_doc: int = 2


class RerankerConfig(BaseModel):
    enabled: bool = True
    model: str = ""
    candidates: int = 10
    concurrency: int = 4
    keep_alive: str = "30m"
    timeout_seconds: float = 60.0
    max_batch_seconds: float = 12.0
    instruct: str = ""


class RetrievalConfig(BaseModel):
    schema_version: int = 1
    collection: str = "documents"
    qdrant_url: str = "http://127.0.0.1:6333"
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    sparse: SparseConfig = Field(default_factory=SparseConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)

    def _apply_env(self) -> RetrievalConfig:
        """Environment overrides for the two network endpoints.

        Only the hosts are overridable — model names and dimensions must match
        what is stored in Qdrant, so they stay pinned to the file.
        """
        if ollama := os.environ.get("OLLAMA_HOST"):
            self.embedding.base_url = ollama.rstrip("/")
        if qdrant := os.environ.get("QDRANT_URL"):
            self.qdrant_url = qdrant.rstrip("/")
        return self


def load_config(path: str | Path | None = None) -> RetrievalConfig:
    """Load and validate ``config/retrieval.yaml`` (or an explicit path)."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise SystemExit(f"Missing retrieval config: {cfg_path}")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return RetrievalConfig.model_validate(raw)._apply_env()


@lru_cache(maxsize=1)
def get_config() -> RetrievalConfig:
    """Process-wide singleton for the default config path."""
    return load_config()
