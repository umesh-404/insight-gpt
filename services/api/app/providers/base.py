"""The pluggable LLM provider interface.

Heavy reasoning (routing, metric selection, synthesis) goes through this
abstraction, so InsightGPT runs fully local by default (Ollama) and upgrades to
a cloud model with a key — no code change, no lock-in. See
``docs/05-insight-engine.md`` §7. No provider is hardcoded; none is a specific
assistant vendor.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """The reasoning model could not be reached, or refused the request.

    Raised instead of returning something. A provider outage must not become a
    templated answer that is indistinguishable from a grounded one: the whole
    claim of this system is that a number traces back to the warehouse and a
    sentence to a document, and a plausible paragraph produced while the model
    was unreachable breaks that claim silently. The API maps this to a 503.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retryable = retryable


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class Usage:
    provider: str
    model: str
    latency_ms: float = 0.0
    prompt_chars: int = 0
    completion_chars: int = 0


class Provider(ABC):
    """Three methods: one-shot completion, multi-turn chat, and a token stream."""

    name: str = "base"

    @abstractmethod
    def complete(self, prompt: str, **opts) -> str: ...

    def chat(self, messages: list[Message], **opts) -> str:
        # Default: flatten to a single prompt. Providers with a native chat API
        # override this.
        prompt = "\n\n".join(f"[{m.role}]\n{m.content}" for m in messages)
        return self.complete(prompt, **opts)

    def stream(self, messages: list[Message], **opts) -> Iterator[str]:
        # Default: no true streaming — yield the whole answer once.
        yield self.chat(messages, **opts)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Tolerantly pull a JSON object out of a model completion.

    Handles fenced ```json blocks and leading/trailing prose. Raises ValueError
    if nothing parseable is found — callers treat that as a model failure.
    """
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        return json.loads(m.group(1))
    # Fall back to the first balanced {...} span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"no JSON object found in completion: {text[:200]!r}")
