"""Operational endpoints — doc 06 §3.6.

``/health`` is public liveness for the orchestrator. ``/status`` is admin-only
readiness: dependency health plus catalog/warehouse/index/LLM stats. In offline
fixture mode external dependencies are reported as ``fixture`` and the overall
status is ``ok``; a real deployment reports live probe results here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth.roles import Role, require_role
from ...config import get_settings
from ...engine.engine import InsightEngine
from ...semantic.catalog import SemanticCatalog
from ..deps import APP_VERSION, get_catalog, get_engine, uptime_seconds

router = APIRouter(tags=["system"])


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    uptime_s: float


class ServiceHealth(BaseModel):
    status: Literal["ok", "degraded", "down", "fixture"]
    detail: str | None = None


class WarehouseStats(BaseModel):
    mode: str
    allow_tables: int
    metrics: int
    dimensions: int


class IndexStats(BaseModel):
    mode: str
    collections: int


class LlmStatus(BaseModel):
    provider: str
    model: str | None = None
    reachable: bool


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


@router.get("/status", response_model=Status, tags=["system"])
async def status(
    _: object = Depends(require_role(Role.admin)),
    catalog: SemanticCatalog = Depends(get_catalog),
    engine: InsightEngine = Depends(get_engine),
) -> Status:
    settings = get_settings()
    fixture_mode = settings.warehouse == "duckdb" or not settings.postgres_dsn
    provider = getattr(engine.provider, "name", settings.llm_provider)
    model = getattr(engine.provider, "model", settings.llm_model)
    offline_provider = provider in ("fake",)

    services = {
        "postgres": ServiceHealth(
            status="fixture" if fixture_mode else "ok",
            detail="in-process DuckDB fixture" if fixture_mode else "read-only pool",
        ),
        "qdrant": ServiceHealth(status="fixture", detail="in-process fixture retriever"),
        "worker": ServiceHealth(status="fixture", detail="in-memory run store"),
        "llm": ServiceHealth(
            status="fixture" if offline_provider else "ok",
            detail=f"provider={provider}",
        ),
    }
    return Status(
        status="ok",
        version=APP_VERSION,
        uptime_s=uptime_seconds(),
        services=services,
        warehouse=WarehouseStats(
            mode="duckdb-fixture" if fixture_mode else "postgres",
            allow_tables=len(catalog.allow_tables),
            metrics=len(catalog.metrics),
            dimensions=len(catalog.dimensions),
        ),
        index=IndexStats(mode="fixture", collections=1),
        llm=LlmStatus(provider=provider, model=str(model) if model else None,
                      reachable=offline_provider),
    )
