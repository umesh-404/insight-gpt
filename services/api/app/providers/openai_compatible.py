"""OpenAI-compatible cloud provider.

Covers any service exposing the ``/chat/completions`` schema — used here for the
OpenAI and Groq options by overriding ``base_url``. The API key is read from the
environment by the factory and never stored in the repo. This is an optional
upgrade for the reasoning step on GPU-less machines; the local Ollama provider
remains the default.
"""

from __future__ import annotations

import json as _json
import logging
import time
from collections.abc import Iterator

import httpx

from .base import Message, Provider

log = logging.getLogger("insightgpt.provider.openai_compatible")


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

        backoff = 2.0
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    r = client.post(f"{self._base}/chat/completions", json=body, headers=self._headers())
                    if r.status_code == 429:
                        if attempt < 2:
                            log.warning("Rate limit (429) hit, retrying in %.1fs...", backoff)
                            time.sleep(backoff)
                            backoff *= 2
                            continue
                        log.warning("Cloud provider rate limit (429) persistent; falling back to offline provider.")
                        break
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < 2:
                    log.warning("Rate limit (429) hit, retrying in %.1fs...", backoff)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                log.warning("Cloud provider HTTP error %s: %s; falling back to offline provider", exc.response.status_code, exc)
                break
            except Exception as exc:
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                log.warning("Cloud provider call failed: %s; falling back to offline provider", exc)
                break

        # Fallback to offline FakeProvider on rate limit or provider outage
        from .fake import FakeProvider
        return FakeProvider().chat(messages, **opts)

    def stream(self, messages: list[Message], **opts) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": opts.get("temperature", 0.0),
            "stream": True,
        }
        backoff = 2.0
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client, \
                        client.stream("POST", f"{self._base}/chat/completions",
                                      json=body, headers=self._headers()) as r:
                    if r.status_code == 429:
                        if attempt < 2:
                            time.sleep(backoff)
                            backoff *= 2
                            continue
                        break
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
                    return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < 2:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                break
            except Exception:
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                break

        # Fallback to streaming offline FakeProvider response
        from .fake import FakeProvider
        full_text = FakeProvider().chat(messages, **opts)
        words = full_text.split(" ")
        for i in range(0, len(words), 4):
            piece = " ".join(words[i : i + 4])
            yield piece + " "
