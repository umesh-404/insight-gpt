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
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

log = logging.getLogger("insightgpt.access")


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

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            _emit(request, status=500, latency_ms=latency_ms)
            raise
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = rid
        _emit(request, status=response.status_code, latency_ms=latency_ms)
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
    trace.append(
        {
            "provider": provider,
            "model": model,
            "operation": operation,
            "latency_ms": round(latency_ms, 2),
            "prompt_chars": prompt_chars,
            "completion_chars": completion_chars,
            "cache_hit": cache_hit,
        }
    )
