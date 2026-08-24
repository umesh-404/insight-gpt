"""Batched embedding via Ollama (POST /api/embed).

Batching is the difference between a usable and an unusable indexer: the model
is resident either way, so the cost is per-request round-trip. Batch size comes
from config.

Two correctness details this module owns, both from rememory's embedder:

* The DOCUMENT vs QUERY prefix is applied here, in one place. These models are
  asymmetric — documents and queries embed with different prompts — and getting
  it wrong degrades retrieval SILENTLY. Callers never see the prefix, so they
  cannot forget it.
* Over-long text is truncated before sending. Ollama silently drops anything
  past the context window, so an un-truncated chunk would embed only its
  beginning while appearing to work.
"""

from __future__ import annotations

import time

import httpx

from .config import EmbeddingConfig


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    def __init__(self, cfg: EmbeddingConfig) -> None:
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self._client = httpx.Client(timeout=cfg.timeout_seconds)
        # Conservative chars-per-token estimate; being wrong here only shortens
        # a chunk slightly, never corrupts it.
        self._max_chars = int(cfg.max_context_tokens * 3.5)

    def __enter__(self) -> Embedder:
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def health(self) -> str:
        """Verify Ollama is up and the configured model is present.

        Checked before indexing so a missing model fails in the first second
        rather than after chunking thousands of documents.
        """
        try:
            resp = self._client.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Ollama unreachable at {self.base_url}: {exc}") from exc

        names = {m["name"] for m in resp.json().get("models", [])}
        if self.cfg.model not in names and f"{self.cfg.model}:latest" not in names:
            raise EmbeddingError(
                f"Model '{self.cfg.model}' not found in Ollama.\n"
                f"Available: {', '.join(sorted(names)) or '(none)'}\n"
                f"Fix with:  ollama pull {self.cfg.model}"
            )
        return self.cfg.model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passage texts, in batches, with the document prefix applied."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.cfg.batch_size):
            batch = texts[i : i + self.cfg.batch_size]
            prepared = [self.cfg.document_prefix + self._truncate(t) for t in batch]
            out.extend(self._call(prepared))
        return out

    _QUERY_CACHE_MAX = 256

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query, with the (different) query prefix applied."""
        cache = getattr(self, "_query_cache", None)
        if cache is None:
            cache = self._query_cache = {}
        if text in cache:
            return cache[text]
        vector = self._call([self.cfg.query_prefix + self._truncate(text)])[0]
        if len(cache) >= self._QUERY_CACHE_MAX:
            cache.pop(next(iter(cache)))
        cache[text] = vector
        return vector

    def _truncate(self, text: str) -> str:
        return text if len(text) <= self._max_chars else text[: self._max_chars]

    def _call(self, inputs: list[str], attempt: int = 0) -> list[list[float]]:
        try:
            resp = self._client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.cfg.model,
                    "input": inputs,
                    "keep_alive": self.cfg.keep_alive,
                },
            )
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
        except (httpx.HTTPError, KeyError) as exc:
            # One or two retries: Ollama occasionally drops a request while
            # swapping a model onto the GPU. Failing a long index over that
            # would be needlessly brittle.
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                return self._call(inputs, attempt + 1)
            raise EmbeddingError(
                f"Embedding failed after {attempt + 1} attempts: {exc}"
            ) from exc

        if len(vectors) != len(inputs):
            raise EmbeddingError(f"Expected {len(inputs)} vectors, got {len(vectors)}")
        if vectors and len(vectors[0]) != self.cfg.dimensions:
            raise EmbeddingError(
                f"Model returned {len(vectors[0])}-d vectors but config says "
                f"{self.cfg.dimensions}. The collection was built for "
                f"{self.cfg.dimensions}; indexing now would corrupt it."
            )
        return vectors
