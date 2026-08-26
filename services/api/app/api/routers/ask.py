"""``POST /ask`` and the conversation transcript reads (doc 06 §3.1, §6, §7).

``/ask`` streams the answer as Server-Sent Events by default, or returns the
assembled :class:`AnswerEnvelope` in one response when the client sends
``Accept: application/json`` or ``stream: false``.

The wrapped engine is synchronous and (with the offline provider) produces the
whole answer at once, so token streaming is done by chunking the final
narrative — the assembled stream is equivalent to the single JSON envelope, as
the contract requires. Every table, citation and caveat in the envelope is
emitted, so a client that reassembles the stream loses nothing.

Both paths persist the turn into the conversation store, which is what backs
``GET /conversations`` and ``GET /conversations/{id}``, plus the two mutations
that keep the history sidebar usable: ``PATCH /conversations/{id}`` (rename)
and ``DELETE /conversations/{id}``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import current_claims
from ...auth.tokens import TokenClaims
from ...config import get_settings
from ...engine.engine import InsightEngine
from ...engine.envelope import AnswerEnvelope
from ...engine.guardrails import GuardrailError
from .. import conversations as store
from ..deps import get_engine, rate_limit
from ..errors import APIError, BadRequestError, NotFoundError
from ..observability import record_llm_call

router = APIRouter(tags=["ask"])

log = logging.getLogger("insightgpt.ask")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    stream: bool = True


class AskError(Exception):
    """An engine failure already reduced to a safe, client-facing shape."""

    def __init__(self, code: str, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _dialect() -> str:
    return "postgres" if get_settings().warehouse != "duckdb" else "duckdb"


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
    """Run the engine, reducing any failure to a sanitized :class:`AskError`.

    A guardrail rejection is the caller's problem (400); anything else is ours
    (500). Neither carries the original exception text to the client — the
    request id in the envelope is the key into the full server-side log line.
    """
    try:
        return await run_in_threadpool(engine.ask, question)
    except GuardrailError:
        log.warning("ask rejected by guardrails", exc_info=True)
        raise AskError(
            "guardrail_rejected",
            "That question could not be answered within the governed data boundary.",
            status_code=400,
        ) from None
    except Exception:
        log.exception("ask failed")
        raise AskError("internal_error", "Failed to answer the question.") from None


def _persist(
    claims: TokenClaims, conversation_id: str, question: str, message_id: str,
    env: AnswerEnvelope,
) -> None:
    """Append the turn to the caller's conversation; never fail the request."""
    try:
        store.append_turn(
            user_id=claims.sub,
            conversation_id=conversation_id,
            question=question,
            message_id=message_id,
            envelope=env,
        )
    except Exception:  # noqa: BLE001 — history is best-effort, the answer is not
        log.exception("failed to persist conversation turn")


@router.post("/ask", dependencies=[Depends(rate_limit("ask"))])
async def ask(
    body: AskRequest,
    request: Request,
    response: Response,
    claims: TokenClaims = Depends(current_claims),
    engine: InsightEngine = Depends(get_engine),
):
    conversation_id = body.conversation_id or store.new_id("c")
    message_id = store.new_id("m")

    accept = request.headers.get("accept", "")
    wants_json = not body.stream or "application/json" in accept.lower()

    if wants_json:
        started = time.perf_counter()
        try:
            env = await _run(engine, body.question)
        except AskError as exc:
            if exc.status_code == 400:
                raise BadRequestError(exc.message) from None
            raise APIError(exc.message) from None
        _trace(request, engine, started, env)
        _persist(claims, conversation_id, body.question, message_id, env)
        # The SSE path carries these in its `meta` event; JSON clients get them
        # as headers so they can continue the thread without polluting the
        # typed envelope (which must stay equal to the assembled stream).
        response.headers["X-Conversation-Id"] = conversation_id
        response.headers["X-Message-Id"] = message_id
        return env  # typed AnswerEnvelope — drives the OpenAPI schema

    async def event_stream() -> AsyncIterator[str]:
        started = time.perf_counter()
        rid = getattr(request.state, "request_id", "-")
        yield _sse("meta", {"conversation_id": conversation_id, "message_id": message_id})
        try:
            env = await _run(engine, body.question)
        except AskError as exc:
            yield _sse("error", {"code": exc.code, "message": exc.message, "request_id": rid})
            return

        for piece in _chunk_text(env.answer):
            yield _sse("token", {"text": piece})

        if env.sql:
            # One event carrying every statement, joined losslessly: the client
            # overwrites on each `sql` event, so splitting them would drop all
            # but the last.
            yield _sse("sql", {"sql": ";\n\n".join(env.sql), "dialect": _dialect()})
        # Every table, not just the primary one — the by-region / by-category
        # breakdowns are part of the answer, and the client appends per event.
        for table in env.tables:
            yield _sse(
                "tables", {"name": table.title, "columns": table.columns, "rows": table.rows}
            )
        if env.citations:
            yield _sse("citations", {"items": [c.model_dump() for c in env.citations]})
        if env.chart is not None:
            yield _sse("chart", {"chart_spec": env.chart.model_dump()})
        if env.caveats:
            yield _sse("caveats", {"items": env.caveats})
        # Self-correction record (bounded) — observable for the eval harness.
        if env.attempts:
            yield _sse("corrections", {"items": [a.model_dump() for a in env.attempts]})
        # Abstention: a valid, honest "I can't answer this reliably" outcome. No
        # number is emitted; the client shows the reason + suggestions.
        if env.abstained:
            yield _sse("abstain", {
                "reason": env.abstain_reason, "suggestions": env.suggestions,
            })
        # Route + confidence complete the envelope for a client assembling it
        # from the stream alone.
        yield _sse("route", {
            "route": env.route, "confidence": env.confidence, "abstained": env.abstained,
        })

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        _trace(request, engine, started, env)
        _persist(claims, conversation_id, body.question, message_id, env)
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


@router.get("/conversations", response_model=store.ConversationPage)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    claims: TokenClaims = Depends(current_claims),
) -> store.ConversationPage:
    return store.list_conversations(claims.sub, limit=limit, offset=offset)


@router.get("/conversations/{conversation_id}", response_model=store.Conversation)
async def get_conversation(
    conversation_id: str = Path(min_length=1, max_length=200),
    claims: TokenClaims = Depends(current_claims),
) -> store.Conversation:
    # A conversation owned by someone else is reported as missing, not
    # forbidden, so ids cannot be probed for existence across users.
    conversation = store.get_conversation(claims.sub, conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation


class RenameConversationRequest(BaseModel):
    """``PATCH /conversations/{id}`` body.

    ``max_length`` is enforced here so an over-long title is a clean 422 with
    the offending field named, rather than a silent truncation.
    """

    title: str = Field(min_length=1, max_length=store.TITLE_INPUT_MAX_CHARS)


@router.patch("/conversations/{conversation_id}", response_model=store.ConversationSummary)
async def rename_conversation(
    body: RenameConversationRequest,
    conversation_id: str = Path(min_length=1, max_length=200),
    claims: TokenClaims = Depends(current_claims),
) -> store.ConversationSummary:
    title = store.normalize_title(body.title)
    if not title:
        raise BadRequestError("Title must contain at least one non-whitespace character.")
    # Same 404-not-403 rule as the read path: someone else's id must not be
    # distinguishable from one that never existed.
    summary = store.rename_conversation(claims.sub, conversation_id, title)
    if summary is None:
        raise NotFoundError("Conversation not found.")
    return summary


@router.delete("/conversations/{conversation_id}", status_code=200)
async def delete_conversation(
    conversation_id: str = Path(min_length=1, max_length=200),
    claims: TokenClaims = Depends(current_claims),
) -> dict[str, str]:
    if not store.delete_conversation(claims.sub, conversation_id):
        raise NotFoundError("Conversation not found.")
    return {"status": "deleted", "id": conversation_id}


def _trace(request: Request, engine: InsightEngine, started: float, env: AnswerEnvelope) -> None:
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
