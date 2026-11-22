"""OpenAI-compatible cloud provider — the production reasoning path.

Covers any service exposing the ``/chat/completions`` schema, which is how
InsightGPT reaches Gemini (Google AI Studio's compatibility endpoint), OpenAI
and Groq through one class. The API key is read from the environment by the
factory and never stored in the repo, logged, or echoed into an error.

Three things here exist because this is production rather than a demo:

* **A pooled client.** Opening an ``httpx.Client`` per call meant a fresh TLS
  handshake on every routing and synthesis request — two per question, each
  paying the round trip. One module-level client keeps the connection warm.
* **Retries that distinguish fault from refusal.** A 429 or a 5xx is the
  provider asking us to wait; a 400 or a 401 is the provider telling us the
  request or the key is wrong and will be wrong again. Only the first kind is
  retried, with exponential backoff, full jitter, and ``Retry-After`` honoured
  when the provider sends it.
* **A typed failure.** Everything that goes wrong surfaces as
  :class:`ProviderError`, which the API turns into a 503. It never degrades to
  a templated answer: this product's claim is that a number can be traced back
  to the warehouse and a sentence to a document, and prose generated from a
  template while the model is unreachable looks exactly like prose that was
  grounded. Refusing is the honest failure.
"""

from __future__ import annotations

import json as _json
import logging
import random
import re
import time
from collections.abc import Iterator

import httpx

from .base import Message, Provider, ProviderError

log = logging.getLogger("insightgpt.provider")

# Status codes worth trying again: rate limiting, and the transient 5xx family.
# 501 and 505 are excluded - a service that does not implement the method will
# not implement it on the retry either.
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

# Credential shapes seen across these providers: OpenAI (`sk-`), Google AI
# Studio (`AIza`), and the generic `apikey_`/`key-` prefixes proxies use. The
# length bound is loose because providers lengthen their keys over time.
_KEY_SHAPED = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{8,}|(?:api)?key[_\-][A-Za-z0-9_\-]{8,})",
    re.IGNORECASE,
)

_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 8.0
# A provider that asks us to wait longer than this is in an outage, not a
# throttle; failing fast beats holding a request open past the client timeout.
_MAX_HONOURED_RETRY_AFTER_S = 20.0


def _sleep_for(attempt: int, retry_after: str | None) -> float:
    """Seconds to wait before ``attempt`` (1-based), honouring ``Retry-After``."""
    if retry_after:
        try:
            # Only the delta-seconds form is handled; the HTTP-date form is rare
            # on these APIs and a wrong parse would sleep for hours.
            wait = float(retry_after.strip())
        except ValueError:
            wait = 0.0
        if 0 < wait <= _MAX_HONOURED_RETRY_AFTER_S:
            return wait
    # Full jitter: without it, every concurrent request retries on the same
    # schedule and re-creates the burst that caused the 429.
    ceiling = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**attempt))
    return random.uniform(0, ceiling)


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        name: str,
        model: str,
        api_key: str,
        base_url: str,
        timeout: float = 60.0,
        max_retries: int = 3,
        max_output_tokens: int | None = 2048,
        max_tokens_field: str = "max_tokens",
    ):
        self.name = name
        self.model = model
        self._key = api_key
        self._base = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.max_output_tokens = max_output_tokens
        self.max_tokens_field = max_tokens_field
        # Connect is capped separately: a hung TCP connect should not consume
        # the whole budget that the model needs to actually generate.
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def health_check(self, timeout: float = 5.0) -> tuple[bool, str]:
        """Ask the provider whether the key works and the model still exists.

        ``/models`` is the cheapest honest question available: it is unmetered
        on all three services, it fails on a revoked key, and it is the only
        way to catch the failure this layer is most exposed to - a model id
        that was valid at deploy time and has since been retired. Reporting a
        cloud provider as healthy because a key is *present* is a false green;
        it says nothing about whether a question would succeed.

        Returns ``(ok, detail)`` and never raises: an unreachable provider is
        the answer here, not an exception.
        """
        try:
            response = self._client.get(f"{self._base}/models", timeout=timeout)
        except httpx.HTTPError as exc:
            return False, self._redact(f"unreachable ({type(exc).__name__})")
        if response.status_code >= 400:
            # Gemini answers 400 (not 401) for a bad key, so the status alone
            # does not say what is wrong - the provider's own sentence does.
            detail = self._redact(_error_detail(response))
            if response.status_code in (401, 403):
                return False, f"the API key was rejected. {detail}".strip()
            return False, f"HTTP {response.status_code}. {detail}".strip()
        try:
            ids = {str(m.get("id", "")) for m in (response.json().get("data") or [])}
        except (ValueError, AttributeError, TypeError):
            return True, "reachable (model list unreadable)"
        if not ids:
            return True, "reachable (model list empty)"
        # Providers prefix ids inconsistently ("models/gemini-2.5-flash"), so
        # match on the trailing segment rather than demanding an exact string.
        wanted = self.model.rsplit("/", 1)[-1]
        if any(i == self.model or i.rsplit("/", 1)[-1] == wanted for i in ids):
            return True, f"reachable; model {self.model!r} available"
        return False, (
            f"reachable, but model {self.model!r} is not in this provider's "
            f"catalogue ({len(ids)} models offered). Check LLM_MODEL."
        )

    # ---- request construction ------------------------------------------------
    def _body(self, messages: list[Message], opts: dict, *, stream: bool) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": opts.get("temperature", 0.0),
        }
        if stream:
            body["stream"] = True
        if self.max_output_tokens:
            # An unbounded completion is an unbounded bill, so a ceiling always
            # goes out - but under exactly ONE name. Sending `max_tokens` and
            # `max_completion_tokens` together to cover both spellings is not
            # belt-and-braces, it is a 400: Gemini rejects the pair outright
            # ("max_tokens and max_completion_tokens cannot both be set"), so
            # that well-meant redundancy failed every single call. Verified
            # against the live endpoint; each name alone is accepted.
            # `LLM_MAX_TOKENS_FIELD` switches it for OpenAI's newer reasoning
            # models, which accept only `max_completion_tokens`.
            body[self.max_tokens_field] = self.max_output_tokens
        if opts.get("json"):
            # Advisory. OpenAI documents this; Gemini's compatibility layer does
            # not document it for chat completions, so the caller must not
            # assume the body is JSON because this was set. `extract_json`
            # tolerates fenced and prose-wrapped output for exactly that reason.
            body["response_format"] = {"type": "json_object"}
        return body

    def _fail(self, detail: str, *, retryable: bool, status: int | None = None) -> ProviderError:
        # The URL carries no secret (the key is a header) but the message is
        # user-visible through the error envelope, so it names the provider and
        # the condition and nothing else.
        return ProviderError(
            f"{self.name}: {detail}", provider=self.name, status=status, retryable=retryable
        )

    # ---- non-streaming -------------------------------------------------------
    def complete(self, prompt: str, **opts) -> str:
        return self.chat([Message(role="user", content=prompt)], **opts)

    def chat(self, messages: list[Message], **opts) -> str:
        body = self._body(messages, opts, stream=False)
        started = time.monotonic()
        response = self._post("/chat/completions", body)
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise self._fail(
                f"returned a response in an unexpected shape ({type(exc).__name__})",
                retryable=False,
            ) from exc
        if text is None:
            # Reasoning models can spend the whole output budget on thinking and
            # return an empty message. Silently treating that as an answer would
            # surface as a blank reply rather than a fault.
            raise self._fail(
                "returned an empty completion, which usually means the output "
                "token budget was consumed before any text was produced",
                retryable=False,
            )
        usage = payload.get("usage") or {}
        log.info(
            "llm call provider=%s model=%s ms=%.0f prompt_tokens=%s completion_tokens=%s",
            self.name, self.model, (time.monotonic() - started) * 1000,
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
        )
        return text

    def _post(self, path: str, body: dict) -> httpx.Response:
        """POST with backoff. Raises :class:`ProviderError` once hope runs out."""
        url = f"{self._base}{path}"
        last: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(url, json=body)
            except httpx.TimeoutException as exc:
                last = self._fail(f"timed out after {self.timeout:.0f}s", retryable=True)
                log.warning("llm timeout provider=%s attempt=%d: %s", self.name, attempt + 1, exc)
            except httpx.HTTPError as exc:
                last = self._fail(f"was unreachable ({type(exc).__name__})", retryable=True)
                log.warning("llm transport provider=%s attempt=%d: %s", self.name, attempt + 1, exc)
            else:
                if response.status_code < 400:
                    return response
                last = self._classify(response)
                if not last.retryable:
                    raise last
                log.warning(
                    "llm http provider=%s status=%d attempt=%d",
                    self.name, response.status_code, attempt + 1,
                )
                if attempt < self.max_retries:
                    time.sleep(_sleep_for(attempt + 1, response.headers.get("retry-after")))
                continue
            if attempt < self.max_retries:
                time.sleep(_sleep_for(attempt + 1, None))
        raise last or self._fail("could not be reached", retryable=True)

    def _redact(self, text: str) -> str:
        """Strip anything credential-shaped out of upstream error text.

        This text is third-party and it reaches the browser through the error
        envelope. Providers echo request context back in error bodies, so the
        realistic leak is not us logging the key but the other side handing it
        back to us and this code passing it straight through. The exact key is
        removed first; the patterns then catch a *different* credential that an
        upstream proxy might quote.
        """
        if not text:
            return text
        # A credential short enough to appear inside ordinary words is not a
        # credential; replacing it blindly turned "a valid API key" into
        # "a valid API [redacted]ey". Only substrings long enough to be real
        # are scrubbed by exact match; the patterns below do the rest.
        if len(self._key) >= 8:
            text = text.replace(self._key, "[redacted]")
        return _KEY_SHAPED.sub("[redacted]", text)

    def _classify(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        # The provider's own message is the most useful diagnostic there is, but
        # it is third-party text going into our envelope: take a bounded slice
        # and scrub it first.
        detail = self._redact(_error_detail(response))
        if status == 401 or status == 403:
            return self._fail(
                "rejected the API key. Check the key for this provider is set "
                "and still valid.", retryable=False, status=status,
            )
        if status == 404:
            return self._fail(
                f"does not recognise model {self.model!r}. Provider catalogues "
                f"change; check LLM_MODEL against the provider's model list. "
                f"{detail}".strip(), retryable=False, status=status,
            )
        if status == 429:
            return self._fail("rate-limited the request", retryable=True, status=status)
        if status in _RETRY_STATUS:
            return self._fail(f"is unavailable (HTTP {status})", retryable=True, status=status)
        return self._fail(
            f"rejected the request (HTTP {status}). {detail}".strip(),
            retryable=False, status=status,
        )

    # ---- streaming -----------------------------------------------------------
    def stream(self, messages: list[Message], **opts) -> Iterator[str]:
        body = self._body(messages, opts, stream=True)
        url = f"{self._base}/chat/completions"
        try:
            with self._client.stream("POST", url, json=body) as r:
                if r.status_code >= 400:
                    r.read()
                    raise self._classify(r)
                for line in r.iter_lines():
                    piece = _sse_delta(line)
                    if piece:
                        yield piece
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise self._fail(f"timed out after {self.timeout:.0f}s", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise self._fail(f"was unreachable ({type(exc).__name__})", retryable=True) from exc


def _error_detail(response: httpx.Response) -> str:
    """The provider's own explanation, across the shapes they actually send.

    OpenAI answers ``{"error": {"message": ...}}``. Gemini's compatibility
    layer answers with that object wrapped in a **list**, which made the naive
    ``.get("error")`` raise and silently discard the text - so an invalid key
    reported as a bare "HTTP 400" with the actual words "Please pass a valid
    API key" thrown away, which is the one sentence an operator needs.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, str):
        return error[:200]
    if isinstance(error, dict):
        return str(error.get("message") or "")[:200]
    return str(payload.get("message") or "")[:200]


def _sse_delta(line: str) -> str:
    """One content delta from an SSE line, or ``""`` for anything else.

    Deliberately forgiving. The compatibility layers do not all document their
    wire format, and a keep-alive comment, a blank line or a chunk carrying only
    a finish reason is normal traffic — not a reason to abort a half-written
    answer. A malformed chunk is skipped rather than raised, because the stream
    has already delivered tokens the reader can see.
    """
    if not line or not line.startswith("data:"):
        return ""
    data = line[len("data:"):].strip()
    if not data or data == "[DONE]":
        return ""
    try:
        choices = _json.loads(data).get("choices") or []
        return choices[0].get("delta", {}).get("content") or "" if choices else ""
    except (ValueError, AttributeError, KeyError, IndexError, TypeError):
        return ""
