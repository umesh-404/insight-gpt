"""Embeddings from an OpenAI-compatible ``/embeddings`` endpoint.

This exists because the local path cannot be deployed everywhere. The Ollama
embedder needs a model server; a managed host usually has nowhere to run one,
so without a cloud option the retriever falls back to keyword matching while
the rest of the product still presents dense retrieval with reranking. That is
the same shape of dishonesty as serving templated prose during a model outage,
and it gets the same answer: make the real thing reachable, and fail loudly
when it is not.

Covers any service speaking the OpenAI embeddings schema — OpenAI, Gemini's
compatibility endpoint, Groq. See ``embedder.make_embedder`` for selection.
"""

from __future__ import annotations

import os
import time

import httpx

from .config import EmbeddingConfig
from .embedder import EmbeddingError


def scrub(text: str, key: str) -> str:
    """Keep the API key out of an exception that may reach a log or a client."""
    return text.replace(key, "[redacted]") if key and len(key) >= 8 else text


class CloudEmbedder:
    """Deliberately the same surface as :class:`~retrieval.embedder.Embedder`.

    ``health``, ``embed_documents``, ``embed_query`` and the context manager
    match, so the indexer and the retriever never learn which backend they are
    holding.

    Two differences from the Ollama path are correctness, not style:

    * **No prefixes.** The asymmetric ``search_document:`` / ``search_query:``
      prompts belong to the nomic-style models. Sent to a cloud model they do
      not error — they embed those literal words into every vector and cost
      recall silently, which is exactly the failure the Ollama embedder's own
      docstring warns about. The config clears them for this provider and this
      class does not re-add them.
    * **The vector width is asserted on the first call.** These models emit
      different widths, so a provider swap against a collection built for the
      old width would write corrupt vectors into Qdrant. That fails here with
      the number to set, rather than degrading search quietly.
    """

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        key = (os.getenv(cfg.api_key_env) or "").strip()
        if not key:
            raise EmbeddingError(
                f"Embedding provider {cfg.provider!r} needs {cfg.api_key_env} in "
                "the environment (not in a config file)."
            )
        self._key = key
        self._client = httpx.Client(
            timeout=cfg.timeout_seconds,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        # Conservative chars-per-token estimate; being wrong here only shortens
        # a chunk slightly, never corrupts it.
        self._max_chars = int(cfg.max_context_tokens * 3.5)
        self._query_cache: dict[str, list[float]] = {}

    def __enter__(self) -> CloudEmbedder:
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def health(self) -> str:
        """Check the key works and the model is offered, before a long index.

        Same contract as the Ollama embedder's: fail in the first second
        rather than after chunking thousands of documents.
        """
        try:
            resp = self._client.get(f"{self.base_url}/models", timeout=15)
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"Embedding endpoint unreachable at {self.base_url}: "
                f"{scrub(str(exc), self._key)}"
            ) from exc
        if resp.status_code >= 400:
            raise EmbeddingError(
                f"Embedding endpoint rejected the request (HTTP "
                f"{resp.status_code}). Check {self.cfg.api_key_env}."
            )
        try:
            ids = {str(m.get("id", "")) for m in (resp.json().get("data") or [])}
        except (ValueError, AttributeError, TypeError):
            return self.cfg.model  # list unreadable; the embed call will say
        if not ids:
            return self.cfg.model
        # Providers prefix ids inconsistently ("models/gemini-embedding-001").
        wanted = self.cfg.model.rsplit("/", 1)[-1]
        if not any(i == self.cfg.model or i.rsplit("/", 1)[-1] == wanted for i in ids):
            raise EmbeddingError(
                f"Model '{self.cfg.model}' is not offered by this endpoint. "
                f"Set EMBED_MODEL to one of the {len(ids)} models it lists."
            )
        return self.cfg.model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passage texts, in batches."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.cfg.batch_size):
            batch = [
                self.cfg.document_prefix + self._truncate(t)
                for t in texts[i : i + self.cfg.batch_size]
            ]
            out.extend(self._call(batch))
        return out

    _QUERY_CACHE_MAX = 256

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query, memoised — the same query recurs constantly."""
        if text in self._query_cache:
            return self._query_cache[text]
        vector = self._call([self.cfg.query_prefix + self._truncate(text)])[0]
        if len(self._query_cache) >= self._QUERY_CACHE_MAX:
            self._query_cache.pop(next(iter(self._query_cache)))
        self._query_cache[text] = vector
        return vector

    def _truncate(self, text: str) -> str:
        return text if len(text) <= self._max_chars else text[: self._max_chars]

    def _call(self, inputs: list[str], attempt: int = 0) -> list[list[float]]:
        body: dict = {"model": self.cfg.model, "input": inputs}
        if self.cfg.request_dimensions:
            body["dimensions"] = self.cfg.dimensions
        try:
            resp = self._client.post(f"{self.base_url}/embeddings", json=body)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            vectors = [row["embedding"] for row in resp.json()["data"]]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            # Rate limits and 5xx are the normal weather of a shared API, and
            # failing a multi-thousand-document index over one would be
            # needlessly brittle. A 4xx that is not 429 is not retried: it
            # will be the same answer next time.
            if attempt < 3 and _worth_retrying(exc):
                time.sleep(1.5 * (attempt + 1))
                return self._call(inputs, attempt + 1)
            # Prefer the provider's own sentence over httpx's summary. "Please
            # pass a valid API key" tells an operator what to do; "Client
            # error '400 Bad Request' for url ..." does not.
            said = _provider_message(exc)
            detail = said or str(exc)
            raise EmbeddingError(
                f"Embedding failed after {attempt + 1} attempt(s): "
                f"{scrub(detail, self._key)}"
            ) from exc

        if len(vectors) != len(inputs):
            raise EmbeddingError(f"Expected {len(inputs)} vectors, got {len(vectors)}")
        if vectors and len(vectors[0]) != self.cfg.dimensions:
            raise EmbeddingError(
                f"Model returned {len(vectors[0])}-d vectors but config says "
                f"{self.cfg.dimensions}. The collection was built for "
                f"{self.cfg.dimensions}; indexing now would corrupt it. "
                f"Set EMBED_DIMENSIONS={len(vectors[0])} and re-index."
            )
        return vectors


def _provider_message(exc: Exception) -> str:
    """The service's own explanation, across the shapes they send.

    OpenAI answers ``{"error": {"message": ...}}``; Gemini's compatibility
    layer wraps that object in a **list**, which makes the obvious
    ``.json()["error"]`` raise and throw the text away.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        return ""
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")[:200]
    if isinstance(error, str):
        return error[:200]
    return ""


def _worth_retrying(exc: Exception) -> bool:
    """A refusal is not a fault. Only transport faults and 429/5xx retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.HTTPError)
