"""``POST /ask`` — the primary endpoint (doc 06 §3.1, §6, §7).

Streams the answer as Server-Sent Events by default, or returns the assembled
:class:`AnswerEnvelope` in one response when the client sends
``Accept: application/json`` or ``stream: false``.

The wrapped engine is synchronous and (with the offline provider) produces the
whole answer at once, so token streaming is done by chunking the final
narrative — the assembled stream is equivalent to the single JSON envelope, as
the contract requires.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import current_claims
from ...auth.tokens import TokenClaims
from ...config import get_settings
from ...engine.engine import InsightEngine
from ...engine.envelope import AnswerEnvelope
from ..deps import get_engine
from ..observability import record_llm_call

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    stream: bool = True


def _dialect() -> str:
    return "postgres" if get_settings().warehouse != "duckdb" else "duckdb"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _chunk_text(text: str, size: int = 6) -> list[str]:
    """Split the narrative into small ordered pieces for ``token`` events."""
    words = text.split(" ")
    chunks: list[str] = []
    for i in range(0, len(words), size):
        piece = " ".join(words[i : i + size])
        # Preserve the inter-chunk space so concatenation reproduces the text.
        chunks.append(piece if i + size >= len(words) else piece + " ")
    return chunks or [text]


async def _run(engine: InsightEngine, question: str) -> AnswerEnvelope:
    return await run_in_threadpool(engine.ask, question)


@router.post("/ask")
async def ask(
    body: AskRequest,
    request: Request,
    claims: TokenClaims = Depends(current_claims),
    engine: InsightEngine = Depends(get_engine),
):
    conversation_id = body.conversation_id or _new_id("c")
    message_id = _new_id("m")

    accept = request.headers.get("accept", "")
    wants_json = not body.stream or "application/json" in accept.lower()

    if wants_json:
        started = time.perf_counter()
        env = await _run(engine, body.question)
        _trace(request, engine, started, env)
        return env  # typed AnswerEnvelope — drives the OpenAPI schema

    async def event_stream() -> AsyncIterator[str]:
        started = time.perf_counter()
        yield _sse("meta", {"conversation_id": conversation_id, "message_id": message_id})
        try:
            env = await _run(engine, body.question)
        except Exception:  # noqa: BLE001 — terminal error is part of the contract
            rid = getattr(request.state, "request_id", "-")
            yield _sse(
                "error",
                {"code": "internal_error", "message": "Failed to answer.", "request_id": rid},
            )
            return

        for piece in _chunk_text(env.answer):
            yield _sse("token", {"text": piece})

        if env.sql:
            yield _sse("sql", {"sql": ";\n\n".join(env.sql), "dialect": _dialect()})
        if env.tables:
            t = env.tables[0]
            yield _sse("tables", {"name": t.title, "columns": t.columns, "rows": t.rows})
        if env.citations:
            yield _sse(
                "citations",
                {"items": [c.model_dump() for c in env.citations]},
            )
        if env.chart is not None:
            yield _sse("chart", {"chart_spec": env.chart.model_dump()})
        if env.caveats:
            yield _sse("caveats", {"items": env.caveats})

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        _trace(request, engine, started, env)
        yield _sse(
            "done",
            {
                "message_id": message_id,
                "usage": {"latency_ms": latency_ms, "confidence": env.confidence},
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": getattr(request.state, "request_id", "-"),
        },
    )


def _trace(
    request: Request, engine: InsightEngine, started: float, env: AnswerEnvelope
) -> None:
    provider = getattr(engine.provider, "name", "unknown")
    model = getattr(engine.provider, "model", provider)
    record_llm_call(
        request,
        provider=provider,
        model=str(model),
        operation="ask",
        latency_ms=(time.perf_counter() - started) * 1000,
        completion_chars=len(env.answer),
    )
