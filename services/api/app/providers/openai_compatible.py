"""OpenAI-compatible cloud provider.

Covers any service exposing the ``/chat/completions`` schema — used here for the
OpenAI and Groq options by overriding ``base_url``. The API key is read from the
environment by the factory and never stored in the repo. This is an optional
upgrade for the reasoning step on GPU-less machines; the local Ollama provider
remains the default.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from .base import Message, Provider


class OpenAICompatibleProvider(Provider):
    def __init__(self, name: str, model: str, api_key: str, base_url: str,
                 timeout: float = 60.0):
        self.name = name
        self.model = model
        self._key = api_key
        self._base = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def complete(self, prompt: str, **opts) -> str:
        return self.chat([Message(role="user", content=prompt)], **opts)

    def chat(self, messages: list[Message], **opts) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": opts.get("temperature", 0.0),
        }
        if opts.get("json"):
            body["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self._base}/chat/completions", json=body, headers=self._headers())
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    def stream(self, messages: list[Message], **opts) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": opts.get("temperature", 0.0),
            "stream": True,
        }
        import json as _json
        with httpx.Client(timeout=self.timeout) as client, \
                client.stream("POST", f"{self._base}/chat/completions",
                              json=body, headers=self._headers()) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[len("data: "):]
                if data.strip() == "[DONE]":
                    break
                delta = _json.loads(data)["choices"][0].get("delta", {})
                piece = delta.get("content", "")
                if piece:
                    yield piece
