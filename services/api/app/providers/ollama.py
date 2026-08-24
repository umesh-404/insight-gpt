"""Local Ollama provider — the default, no API key required.

Talks to a local Ollama runtime over HTTP. Embeddings and reranking also run on
Ollama (see the retrieval service); this class covers the chat/completion calls
the engine needs. On a CPU-only machine, pointing the engine at a cloud provider
for the reasoning step is the recommended path — this remains the private
default.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from .base import Message, Provider


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model: str = "llama3.1:8b", host: str = "http://127.0.0.1:11434",
                 timeout: float = 120.0):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def complete(self, prompt: str, **opts) -> str:
        return self.chat([Message(role="user", content=prompt)], **opts)

    def chat(self, messages: list[Message], **opts) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": opts.get("temperature", 0.0)},
        }
        if opts.get("json"):
            body["format"] = "json"
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self.host}/api/chat", json=body)
            r.raise_for_status()
            return r.json()["message"]["content"]

    def stream(self, messages: list[Message], **opts) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": opts.get("temperature", 0.0)},
        }
        import json as _json
        with httpx.Client(timeout=self.timeout) as client, \
                client.stream("POST", f"{self.host}/api/chat", json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = _json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
