"""Operational endpoints — doc 06 §3.6.

``/health`` is public liveness for the orchestrator. ``/status`` is admin-only
readiness and reports **real** state: it probes the configured warehouse (and
counts tables/rows where that is cheap), the vector store (collection + point
count), and the LLM provider configuration (name and model — never a key), and
adds the governed catalog size and process uptime.

Every probe is individually guarded: a backend that is down is reported as
``down``/``degraded`` with a short detail and the overall status becomes
``degraded``. ``/status`` never 500s because a dependency is unavailable.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...config import Settings, get_settings
from ...engine.engine import InsightEngine
from ...semantic.catalog import SemanticCatalog
from ..deps import APP_VERSION, get_catalog, get_engine, uptime_seconds

router = APIRouter(tags=["system"])

ServiceStatus = Literal["ok", "degraded", "down", "fixture"]

# Providers whose "reachability" is a local decision, not a network call.
_OFFLINE_PROVIDERS = {"fake"}
# Cloud providers and the env var that carries their key (the key is never read
# into a response — only its presence is reported).
_PROVIDER_KEY_ENV = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}
_PROBE_TIMEOUT_S = 2.0
_MAX_COUNTED_TABLES = 12


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    uptime_s: float


class ServiceHealth(BaseModel):
    status: ServiceStatus
    detail: str | None = None
    latency_ms: float | None = None


class WarehouseStats(BaseModel):
    mode: str
    reachable: bool
    allow_tables: int
    tables_counted: int = 0
    row_counts: dict[str, int] = Field(default_factory=dict)
    total_rows: int | None = None
    metrics: int
    dimensions: int


class IndexStats(BaseModel):
    mode: str
    reachable: bool
    collections: int
    points: int | None = None
    collection: str | None = None


class LlmStatus(BaseModel):
    provider: str
    model: str | None = None
    reachable: bool
    detail: str | None = None
    # Whether a credential is configured. The key itself is never exposed.
    credential_configured: bool | None = None


class Status(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    uptime_s: float
    services: dict[str, ServiceHealth] = Field(default_factory=dict)
    warehouse: WarehouseStats
    index: IndexStats
    llm: LlmStatus


@router.get("/health", response_model=Health, tags=["system"])
async def health() -> Health:
    return Health(version=APP_VERSION, uptime_s=uptime_seconds())


# --- probes (synchronous; always called through run_in_threadpool) ------------


def _probe_warehouse(
    engine: InsightEngine, catalog: SemanticCatalog, settings: Settings
) -> tuple[WarehouseStats, ServiceHealth]:
    fixture_mode = _warehouse_mode(settings) == "duckdb-fixture"
    stats = WarehouseStats(
        mode=_warehouse_mode(settings),
        reachable=False,
        allow_tables=len(catalog.allow_tables),
        metrics=len(catalog.metrics),
        dimensions=len(catalog.dimensions),
    )
    counts: dict[str, int] = {}
    try:
        if fixture_mode:
            # The fixture warehouse is in-process and tiny — exact counts are cheap.
            for table in catalog.allow_tables[:_MAX_COUNTED_TABLES]:
                result = engine.warehouse.run(f"SELECT COUNT(*) AS n FROM {table}", [])
                counts[table] = int(result.rows[0][0]) if result.rows else 0
        else:
            counts = _postgres_row_estimates(settings, catalog)
    except Exception as exc:  # noqa: BLE001 — a down warehouse is a report, not a 500
        return stats, ServiceHealth(
            status="down", detail=_short(f"{type(exc).__name__}: {exc}")
        )

    stats.reachable = True
    stats.row_counts = counts
    stats.tables_counted = len(counts)
    stats.total_rows = sum(counts.values()) if counts else 0
    return stats, ServiceHealth(
        status="fixture" if fixture_mode else "ok",
        detail="in-process DuckDB fixture" if fixture_mode else "read-only pool",
    )


def _postgres_row_estimates(settings: Settings, catalog: SemanticCatalog) -> dict[str, int]:
    """Planner row estimates for the allow-listed tables — O(1), never a scan."""
    import psycopg

    sql = (
        "SELECT c.relname, GREATEST(c.reltuples, 0)::bigint "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p', 'm') AND c.relname = ANY(%s) "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
    )
    with psycopg.connect(
        settings.postgres_dsn, autocommit=True, connect_timeout=int(_PROBE_TIMEOUT_S)
    ) as con:
        con.execute("SET statement_timeout = 2000")
        rows = con.execute(sql, [list(catalog.allow_tables)]).fetchall()
    return {str(name): int(estimate) for name, estimate in rows}


def _probe_index(engine: InsightEngine, settings: Settings) -> tuple[IndexStats, ServiceHealth]:
    retriever = engine.retriever
    if settings.retriever != "qdrant" or not hasattr(retriever, "searcher"):
        docs = getattr(retriever, "_docs", None)
        points = len(docs) if docs is not None else None
        return (
            IndexStats(
                mode="fixture", reachable=True, collections=1,
                points=points, collection="fixture-documents",
            ),
            ServiceHealth(status="fixture", detail="in-process fixture retriever"),
        )

    searcher = retriever.searcher
    collection = getattr(searcher, "collection", None)
    try:
        client = searcher.client
        collections = client.get_collections().collections
        points = None
        if collection and client.collection_exists(collection):
            points = client.get_collection(collection).points_count or 0
    except Exception as exc:  # noqa: BLE001 — an unreachable index is reportable
        return (
            IndexStats(mode="qdrant", reachable=False, collections=0, collection=collection),
            ServiceHealth(status="down", detail=_short(f"{type(exc).__name__}: {exc}")),
        )
    return (
        IndexStats(
            mode="qdrant", reachable=True, collections=len(collections),
            points=points, collection=collection,
        ),
        ServiceHealth(status="ok", detail=f"qdrant collection {collection!r}"),
    )


def _probe_llm(engine: InsightEngine, settings: Settings) -> tuple[LlmStatus, ServiceHealth]:
    provider = str(getattr(engine.provider, "name", settings.llm_provider))
    raw_model = getattr(engine.provider, "model", settings.llm_model)
    model = str(raw_model) if raw_model else None

    if provider in _OFFLINE_PROVIDERS:
        return (
            LlmStatus(provider=provider, model=model, reachable=True,
                      detail="offline deterministic provider"),
            ServiceHealth(status="fixture", detail=f"provider={provider}"),
        )

    if provider == "ollama":
        host = getattr(engine.provider, "host", settings.ollama_host)
        reachable, detail = _probe_http(f"{str(host).rstrip('/')}/api/tags")
        return (
            LlmStatus(provider=provider, model=model, reachable=reachable, detail=detail),
            ServiceHealth(
                status="ok" if reachable else "down", detail=f"provider={provider}"
            ),
        )

    key_env = _PROVIDER_KEY_ENV.get(provider)
    configured = bool(os.getenv(key_env)) if key_env else None
    # No network call for a metered cloud provider; report configuration only.
    return (
        LlmStatus(
            provider=provider, model=model, reachable=bool(configured),
            credential_configured=configured,
            detail="credential configured" if configured else "no credential configured",
        ),
        ServiceHealth(
            status="ok" if configured else "degraded", detail=f"provider={provider}"
        ),
    )


def _probe_http(url: str) -> tuple[bool, str]:
    try:
        import httpx

        response = httpx.get(url, timeout=_PROBE_TIMEOUT_S)
        if response.status_code < 500:
            return True, f"reachable (HTTP {response.status_code})"
        return False, f"HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001 — unreachable is the answer, not an error
        return False, _short(f"{type(exc).__name__}: {exc}")


def _probe_worker(settings: Settings) -> ServiceHealth:
    """The worker owns ``insight.pipeline_runs``; reachability is the run store."""
    if not settings.postgres_dsn:
        return ServiceHealth(status="fixture", detail="in-memory run store")
    try:
        import psycopg

        with psycopg.connect(
            settings.postgres_dsn, autocommit=True, connect_timeout=int(_PROBE_TIMEOUT_S)
        ) as con:
            row = con.execute(
                "SELECT count(*) FROM insight.pipeline_runs "
                "WHERE status IN ('queued', 'running')"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        return ServiceHealth(status="down", detail=_short(f"{type(exc).__name__}: {exc}"))
    active = int(row[0]) if row else 0
    return ServiceHealth(status="ok", detail=f"{active} active run(s)")


def _warehouse_mode(settings: Settings) -> str:
    if settings.warehouse == "postgres" and settings.postgres_dsn:
        return "postgres"
    return "duckdb-fixture"


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _collect(engine: InsightEngine, catalog: SemanticCatalog, settings: Settings) -> dict[str, Any]:
    warehouse_stats, warehouse_health = _probe_warehouse(engine, catalog, settings)
    index_stats, index_health = _probe_index(engine, settings)
    llm_status, llm_health = _probe_llm(engine, settings)
    return {
        "warehouse": warehouse_stats,
        "index": index_stats,
        "llm": llm_status,
        "services": {
            "postgres": warehouse_health,
            "qdrant": index_health,
            "worker": _probe_worker(settings),
            "llm": llm_health,
        },
    }


@router.get("/status", response_model=Status, tags=["system"])
async def status(
    _: object = Depends(require_role(Role.admin)),
    catalog: SemanticCatalog = Depends(get_catalog),
    engine: InsightEngine = Depends(get_engine),
) -> Status:
    settings = get_settings()
    # Every probe is blocking network/DB work — keep it off the event loop.
    collected = await run_in_threadpool(_collect, engine, catalog, settings)
    services: dict[str, ServiceHealth] = collected["services"]
    overall = "ok" if all(s.status in ("ok", "fixture") for s in services.values()) else "degraded"
    return Status(
        status=overall,
        version=APP_VERSION,
        uptime_s=uptime_seconds(),
        services=services,
        warehouse=collected["warehouse"],
        index=collected["index"],
        llm=collected["llm"],
    )
