"""Proactive insight digest (analyst+) — doc 05 §3.3, the differentiating path.

The system surfaces anomalies and their root causes **without being asked**:

* ``GET  /insights``          — recent insights, newest-first, paginated.
* ``GET  /insights/{id}``     — one insight with its full root cause + evidence.
* ``POST /insights/refresh``  — run detection now and return the fresh set.

When a Postgres insights table is configured (the worker's ``insight_digest``
job fills it on a schedule) reads come from it. Offline, the router generates
insights on demand from the fixture warehouse, so the demo is always populated.
Every failure degrades to on-demand generation — a missing or cold backend is
never a 500.
"""

from __future__ import annotations

import contextlib
import os
import threading
from functools import lru_cache

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...engine.engine import InsightEngine
from ...insights import Insight, detect_insights
from ...insights.store import InsightStore, store_from_env
from ..deps import get_engine, rate_limit
from ..errors import NotFoundError

router = APIRouter(tags=["insights"])

_MAX_LIMIT = 100

# On-demand results are memoized so repeated reads over the offline stack do not
# re-run detection on every request. Cleared by a refresh and by reset_state().
_CACHE: list[Insight] | None = None
_CACHE_LOCK = threading.Lock()


class InsightPage(BaseModel):
    items: list[Insight] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    backend: str  # "postgres" | "file" | "memory (on-demand)"


@lru_cache(maxsize=1)
def get_insight_store() -> InsightStore:
    """The persistence backend, from ``POSTGRES_DSN`` (else in-memory/on-demand)."""
    return store_from_env(file_path=os.getenv("INSIGHTS_FILE") or None)


def reset_state() -> None:
    """Drop the on-demand cache and the cached store (tests)."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None
    get_insight_store.cache_clear()


def _generate(engine: InsightEngine) -> list[Insight]:
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = detect_insights(engine)
        return _CACHE


def _load(engine: InsightEngine, store: InsightStore) -> list[Insight]:
    """Persisted insights when a durable backend has any; else on-demand."""
    if store.available:
        try:
            items, total = store.list(limit=_MAX_LIMIT, offset=0)
            if total > 0:
                return items
        except Exception:  # noqa: BLE001 — a cold/broken backend must not 500
            pass
    return _generate(engine)


@router.get("/insights", response_model=InsightPage)
async def list_insights(
    limit: int = Query(default=20, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _: object = Depends(require_role(Role.analyst)),
    engine: InsightEngine = Depends(get_engine),
    store: InsightStore = Depends(get_insight_store),
) -> InsightPage:
    items = await run_in_threadpool(_load, engine, store)
    backend = store.backend if store.available else "memory (on-demand)"
    return InsightPage(
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
        backend=backend,
    )


@router.get("/insights/{insight_id}", response_model=Insight)
async def get_insight(
    insight_id: str,
    _: object = Depends(require_role(Role.analyst)),
    engine: InsightEngine = Depends(get_engine),
    store: InsightStore = Depends(get_insight_store),
) -> Insight:
    items = await run_in_threadpool(_load, engine, store)
    for item in items:
        if item.id == insight_id:
            return item
    raise NotFoundError(f"No insight with id {insight_id!r}.")


@router.post(
    "/insights/refresh",
    response_model=InsightPage,
    dependencies=[Depends(rate_limit("ask"))],
)
async def refresh_insights(
    limit: int = Query(default=20, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _: object = Depends(require_role(Role.analyst)),
    engine: InsightEngine = Depends(get_engine),
    store: InsightStore = Depends(get_insight_store),
) -> InsightPage:
    global _CACHE
    insights = await run_in_threadpool(detect_insights, engine)
    with _CACHE_LOCK:
        _CACHE = insights
    if store.available:
        # Persistence is best-effort here; a cold backend must not fail refresh.
        with contextlib.suppress(Exception):
            await run_in_threadpool(store.replace_all, insights)
    backend = store.backend if store.available else "memory (on-demand)"
    return InsightPage(
        items=insights[offset : offset + limit],
        total=len(insights),
        limit=limit,
        offset=offset,
        backend=backend,
    )
