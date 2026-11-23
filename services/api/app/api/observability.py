"""Request-scoped observability: request IDs, structured logs, timing/LLM trace.

Every request carries an ``X-Request-ID`` (accepted from the caller or minted),
echoed on the response and stamped on every log line (doc 06 §9). One JSON log
event is emitted per request with ``request_id``, ``user_id``, ``role``,
``route``, ``status``, and ``latency_ms`` — never secrets or raw PII.

The second half of the module is the **dataset lifecycle trail** (phase 2): one
structured line per upload, index stage, completion, failure and delete, so that
when an upload misbehaves in front of an audience the log says which dataset,
which stage, how long it took and how many chunks came out of it — without
putting the document's own content, its full filename, or anything
credential-shaped on a console. Redaction happens at ingestion; the logs are on
the wrong side of that boundary, so :func:`dataset_event` scrubs rather than
trusting its callers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

log = logging.getLogger("insightgpt.access")

# Ambient request id so code with no ``Request`` handle (engine internals,
# background work) can still stamp its logs with the correlating id.
_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    """The id of the request being served on this task, or ``"-"``."""
    return _REQUEST_ID.get()


def configure_logging(level: int = logging.INFO) -> None:
    """Install a single JSON-line stdout handler (idempotent)."""
    root = logging.getLogger("insightgpt")
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, time the request, and emit one structured log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = rid
        request.state.user_id = None
        request.state.role = None
        request.state.llm_trace = []
        token = _REQUEST_ID.set(rid)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            _emit(request, status=500, latency_ms=latency_ms)
            raise
        finally:
            _REQUEST_ID.reset(token)

        response.headers[REQUEST_ID_HEADER] = rid

        # Emit the access line *after* the body has been sent. For a streamed
        # answer the LLM trace is only complete once the generator has finished,
        # so logging inline would drop it (doc 06 §9 wants the trace on the line).
        previous = response.background

        async def _finalize() -> None:
            if previous is not None:
                await previous()
            _emit(
                request,
                status=response.status_code,
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
            )

        response.background = BackgroundTask(_finalize)
        return response


def _emit(request: Request, *, status: int, latency_ms: float) -> None:
    context = {
        "request_id": getattr(request.state, "request_id", "-"),
        "user_id": getattr(request.state, "user_id", None),
        "role": getattr(request.state, "role", None),
        "route": request.url.path,
        "method": request.method,
        "status": status,
        "latency_ms": latency_ms,
    }
    trace = getattr(request.state, "llm_trace", None)
    if trace:
        context["llm_trace"] = trace
    log.info("request", extra={"context": context})


def record_llm_call(
    request: Request,
    *,
    provider: str,
    model: str,
    operation: str,
    latency_ms: float,
    prompt_chars: int = 0,
    completion_chars: int = 0,
    cache_hit: bool = False,
) -> None:
    """Attach an LLM call trace to the current request (doc 06 §9)."""
    trace = getattr(request.state, "llm_trace", None)
    if trace is None:
        return
    entry = {
        "provider": provider,
        "model": model,
        "operation": operation,
        "latency_ms": round(latency_ms, 2),
        "prompt_chars": prompt_chars,
        "completion_chars": completion_chars,
        "cache_hit": cache_hit,
    }
    trace.append(entry)
    log.debug(
        "llm_call",
        extra={"context": {"request_id": getattr(request.state, "request_id", "-"), **entry}},
    )


@contextmanager
def time_llm_call(
    request: Request, *, provider: str, model: str, operation: str, prompt_chars: int = 0
) -> Iterator[dict[str, int]]:
    """Time a block and record it as an LLM call, even when it raises.

    Yields a small mutable dict; set ``result["completion_chars"]`` inside the
    block so the recorded trace carries the real completion size.
    """
    result: dict[str, int] = {"completion_chars": 0}
    started = time.perf_counter()
    try:
        yield result
    finally:
        record_llm_call(
            request,
            provider=provider,
            model=model,
            operation=operation,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_chars=prompt_chars,
            completion_chars=int(result.get("completion_chars", 0)),
        )


# --------------------------------------------------------------------------
# dataset lifecycle trail (phase 2)
# --------------------------------------------------------------------------

dataset_log = logging.getLogger("insightgpt.datasets")

#: Field names that must never reach a log line, whatever a caller passes. The
#: document's own text is the point of the whole redaction boundary, and the
#: filename is excluded in full because users name files after the person,
#: account or case they concern.
_FORBIDDEN_KEYS = frozenset(
    {
        "text",
        "content",
        "body",
        "chunk",
        "chunks_text",
        "snippet",
        "excerpt",
        "payload",
        "filename",
        "file_name",
        "name",
        "path",
        "token",
        "secret",
        "password",
        "api_key",
        "apikey",
        "authorization",
        "credentials",
    }
)

#: ``scheme://user:password@host`` — a connection string in an exception message
#: is the most likely way a credential reaches a log from this path.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)[^/\s:@]+:[^/\s@]+@")

#: A long unbroken run of token characters: keys, JWTs, hashes. Length, not
#: shape, is the signal — nothing legitimate in these messages looks like this.
_TOKEN_SHAPED = re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b")

#: Free-text fields (messages, error reasons) are truncated, not trusted.
_MAX_TEXT = 300


def file_fingerprint(name: str | None) -> dict[str, object]:
    """What may be said about an uploaded file's name, and nothing more.

    Returns the extension (needed to explain a parse failure), the name's length
    and a short stable digest. The digest correlates the same file across runs
    and across lines without ever writing ``Q3-payroll-jane-doe.xlsx`` to a
    console someone is screen-sharing.
    """
    raw = name or ""
    _, _, extension = raw.rpartition(".")
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:10]
    return {
        "file_ext": extension.lower()[:16] if "." in raw else "",
        "file_ref": digest,
        "file_name_chars": len(raw),
    }


def scrub(value: object) -> object:
    """Mask credentials in free text and keep only loggable scalars."""
    if value is None or isinstance(value, bool | int | float):
        return value
    text = value if isinstance(value, str) else repr(value)
    text = _URL_CREDENTIALS.sub(r"\g<scheme>[redacted]@", text)
    text = _TOKEN_SHAPED.sub("[redacted]", text)
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + "…"
    return text


def dataset_event(
    event: str,
    *,
    dataset_id: str | None = None,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit one structured dataset-lifecycle line, stamped with the request id.

    ``event`` is a stable machine name (``dataset_upload``, ``dataset_indexed``,
    ``dataset_failed``, ...). Everything in ``fields`` is scrubbed: forbidden
    keys are dropped and recorded as dropped rather than silently omitted, so a
    reader can tell "nothing was passed" from "something unloggable was".
    """
    context: dict[str, object] = {
        "event": event,
        "request_id": current_request_id(),
    }
    if dataset_id is not None:
        context["dataset_id"] = scrub(dataset_id)
    dropped = sorted(key for key in fields if key.lower() in _FORBIDDEN_KEYS)
    for key, value in fields.items():
        if key.lower() in _FORBIDDEN_KEYS:
            continue
        context[key] = scrub(value)
    if dropped:
        context["dropped_fields"] = ",".join(dropped)
    dataset_log.log(level, event, extra={"context": context})


@contextmanager
def dataset_stage(
    event: str, *, dataset_id: str, **fields: object
) -> Iterator[dict[str, object]]:
    """Time one stage of the dataset pipeline and log it exactly once.

    Logs on the way out whether the stage returned or raised, because a stage
    that raised is the one an operator is looking for. Yields a mutable dict:
    put what the stage measured in it (``result["chunks"] = 420``) and it lands
    on the line. The exception is re-raised untouched — this records, it never
    swallows.

    A stage that handles its own failure (the indexing worker records a failed
    status rather than propagating) can set ``result["outcome"] = "failed"`` and
    ``result["error_type"]``; both override what this measured, and the line is
    logged at WARNING. Nothing a caller puts in ``result`` can collide with a
    keyword here.
    """
    result: dict[str, object] = {}
    started = time.perf_counter()
    outcome = "ok"
    error_type: str | None = None
    try:
        yield result
    except BaseException as exc:  # noqa: BLE001 — re-raised below, only recorded
        outcome, error_type = "failed", type(exc).__name__
        raise
    finally:
        # ``result`` wins over ``fields``: the stage's own observed outcome is
        # more current than what the caller guessed at the start.
        extra: dict[str, object] = {**fields, **result}
        if error_type:
            extra["error_type"] = error_type
        outcome = str(extra.pop("outcome", outcome))
        duration_ms = extra.pop(
            "duration_ms", round((time.perf_counter() - started) * 1000, 2)
        )
        dataset_event(
            event,
            dataset_id=dataset_id,
            level=logging.WARNING if outcome == "failed" else logging.INFO,
            outcome=outcome,
            duration_ms=duration_ms,
            **extra,
        )
