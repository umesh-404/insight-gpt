"""Request-scoped observability: request IDs, structured logs, timing/LLM trace.

Every request carries an ``X-Request-ID`` (accepted from the caller or minted),
echoed on the response and stamped on every log line (doc 06 §9). One JSON log
event is emitted per request with ``request_id``, ``user_id``, ``role``,
``route``, ``status``, and ``latency_ms`` — never secrets or raw PII.
"""

from __future__ import annotations

import json
import logging
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
