"""A minimal Ollama chat helper for the retrieval service.

The service already talks to Ollama for embeddings (:mod:`retrieval.embedder`)
and reranking (:mod:`retrieval.rerank`). Query rewriting and optional
index-time contextual augmentation need one more shape of call — a short chat
completion — so this is the single place that owns it.

Design rule, shared with the reranker: chat is an ENHANCEMENT. Every call is
best-effort and returns ``None`` on any failure (unreachable host, missing
model, timeout, malformed response) rather than raising. The caller always has
a deterministic fallback, so a search or an index must never fail because the
optional LLM step did.
"""

from __future__ import annotations

import sys

import httpx


class OllamaChat:
    """Best-effort single-turn chat completion against Ollama ``/api/chat``."""

    def __init__(self, base_url: str, model: str, timeout_seconds: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = (model or "").strip()
        self.timeout = timeout_seconds
        # A hard failure (model not pulled) disables the client for its lifetime
        # so it does not add a timeout to every subsequent call — the reranker's
        # ``_dead`` policy.
        self._dead = False

    @property
    def usable(self) -> bool:
        return bool(self.model) and not self._dead

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        num_predict: int = 128,
    ) -> str | None:
        """Return the assistant's text, or ``None`` if anything went wrong."""
        if not self.usable:
            return None
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": num_predict},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self._dead = True
                print(
                    f"chat model '{self.model}' not pulled; disabling LLM path. "
                    f"Fix: ollama pull {self.model}",
                    file=sys.stderr,
                )
            else:
                print(f"chat call failed ({exc}); using deterministic fallback", file=sys.stderr)
            return None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"chat call failed ({exc}); using deterministic fallback", file=sys.stderr)
            return None
        text = (content or "").strip()
        return text or None
