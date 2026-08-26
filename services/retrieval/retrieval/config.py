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
    # Deterministic contextual augmentation: fold region/category into the
    # breadcrumb so a chunk that never names its own region still carries that
    # vocabulary in its embedded/sparse text (never in the stored body). Pure
    # function of the document's canonical fields, so it runs fully offline and
    # does not affect the content hash.
    contextual_augmentation: bool = True
    # Optional LLM-written one-line situating context, prepended to the embedded
    # text only. Needs live Ollama; degrades to the deterministic breadcrumb when
    # unavailable. Off by default so indexing stays offline-safe.
    contextual_llm: bool = False
    context_model: str = ""  # chat model for contextual_llm; empty -> reuse rewrite model


class QueryRewriteConfig(BaseModel):
    """Pre-retrieval query rewriting (see :mod:`retrieval.rewrite`).

    ``enabled`` is safe to default on: with no Ollama it uses a deterministic
    rewrite (lowercase, drop filler/stopwords, keep entities, expand
    abbreviations) and never touches the network. ``use_llm`` and ``hyde`` add
    live Ollama paths and default off so retrieval degrades gracefully offline.
    """

    enabled: bool = True
    use_llm: bool = False
    hyde: bool = False
    model: str = ""  # chat model for the LLM rewrite; empty disables the LLM path
    timeout_seconds: float = 20.0
    max_query_chars: int = 512
    # abbreviation -> expansion, merged over the built-in defaults.
    abbreviations: dict[str, str] = Field(default_factory=dict)


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
    query_rewrite: QueryRewriteConfig = Field(default_factory=QueryRewriteConfig)

    def _apply_env(self) -> RetrievalConfig:
        """Environment overrides for the network endpoints and embedding model.

        Hosts (``OLLAMA_HOST``, ``QDRANT_URL``) point a container at sibling
        services. ``EMBED_MODEL`` and ``RERANK_MODEL`` override the model names
        so the shared deployment selector wins over the file default.

        ``EMBED_MODEL`` must name a model whose vectors match ``dimensions`` and
        the query/document prefixes stored in Qdrant, or retrieval degrades
        silently — a real change there requires a full re-index.
        """
        if ollama := os.environ.get("OLLAMA_HOST"):
            self.embedding.base_url = ollama.rstrip("/")
        if qdrant := os.environ.get("QDRANT_URL"):
            self.qdrant_url = qdrant.rstrip("/")
        if embed_model := os.environ.get("EMBED_MODEL"):
            self.embedding.model = embed_model
        # `RERANK_MODEL` is the variable bootstrap PULLS, so it has to be the
        # variable the reranker USES — otherwise changing it in .env pulls one
        # model and scores with another, and the mismatch shows up only as a
        # 404 that quietly disables reranking. An empty value disables reranking
        # explicitly, which is a legitimate deployment choice on a small box.
        rerank_model = os.environ.get("RERANK_MODEL")
        if rerank_model is not None:
            self.reranker.model = rerank_model.strip()
            if not self.reranker.model:
                self.reranker.enabled = False
        # Query rewriting: an ops switch that mirrors the config flags so a
        # deployment can toggle the pre-retrieval step without editing the file.
        # The LLM/HyDE paths also require ``RETRIEVAL_LIVE=1`` so they never fire
        # in an offline test or CI run even if the config leaves them on.
        if (flag := _env_bool("QUERY_REWRITE")) is not None:
            self.query_rewrite.enabled = flag
        if chat_model := os.environ.get("REWRITE_MODEL"):
            self.query_rewrite.model = chat_model.strip()
        if os.environ.get("RETRIEVAL_LIVE") != "1":
            # Offline/CI: force the deterministic path, never call Ollama chat.
            self.query_rewrite.use_llm = False
            self.query_rewrite.hyde = False
            self.chunking.contextual_llm = False
        return self


def _env_bool(name: str) -> bool | None:
    """Parse a boolean env flag; ``None`` when unset so callers keep the default."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
