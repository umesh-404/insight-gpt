"""Governed metrics: the semantic catalog and the direct (non-LLM) query path.

``GET /metrics`` lists the governed metrics and dimensions the caller may query
(doc 06 §3.2) — the allow-list for ``POST /metrics/query``. The query endpoint
(analyst+) accepts a governed selection (metric, dimensions, filters, grain),
compiles it through the existing ``query_builder`` + guardrails, and executes it
read-only against the warehouse. It never accepts SQL.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth.roles import Role, require_role
from ...semantic.catalog import CatalogError, SemanticCatalog
from ...semantic.query_builder import Filter, MetricSelection, build_query
from ...warehouse.executor import Warehouse
from ..deps import get_catalog, get_warehouse
from ..errors import BadRequestError

router = APIRouter(tags=["metrics"])

# format -> the unit vocabulary the frontend expects (doc 06 §3.2).
_UNIT = {
    "currency": "currency",
    "integer": "count",
    "number": "count",
    "percent": "ratio",
}


class MetricDef(BaseModel):
    key: str
    label: str
    description: str
    unit: str
    grain: list[str]
    default_agg: str


class DimensionDef(BaseModel):
    key: str
    grains: list[str] = Field(default_factory=list)
    is_date: bool = False


class MetricsCatalog(BaseModel):
    metrics: list[MetricDef]
    dimensions: list[DimensionDef]


class TimeRange(BaseModel):
    grain: str | None = None
    start: str
    end: str


class MetricQuery(BaseModel):
    metric: str
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    time_range: TimeRange | None = None
    order: Literal["asc", "desc"] | None = None
    limit: int = Field(default=1000, le=10000, ge=1)


class ColumnSpec(BaseModel):
    name: str
    dtype: str


class MetricResult(BaseModel):
    columns: list[ColumnSpec]
    rows: list[list[Any]]
    sql: str
    row_count: int
    truncated: bool


@router.get("/metrics", response_model=MetricsCatalog)
async def list_metrics(
    _: object = Depends(require_role(Role.viewer)),
    catalog: SemanticCatalog = Depends(get_catalog),
) -> MetricsCatalog:
    metrics = [
        MetricDef(
            key=m.name,
            label=m.label,
            description=m.label,
            unit=_UNIT.get(m.format, "count"),
            grain=list(m.dimensions),
            default_agg="ratio" if not m.additive else "sum",
        )
        for m in catalog.metrics.values()
    ]
    dimensions = [
        DimensionDef(key=d.name, grains=sorted(d.grains), is_date=d.is_date())
        for d in catalog.dimensions.values()
    ]
    return MetricsCatalog(metrics=metrics, dimensions=dimensions)


@router.post("/metrics/query", response_model=MetricResult)
async def query_metric(
    body: MetricQuery,
    _: object = Depends(require_role(Role.analyst)),
    catalog: SemanticCatalog = Depends(get_catalog),
    warehouse: Warehouse = Depends(get_warehouse),
) -> MetricResult:
    selection = _to_selection(body)
    try:
        built = build_query(selection, catalog)
        result = warehouse.run(built.sql, built.params)
    except CatalogError as exc:
        raise BadRequestError(str(exc)) from exc

    truncated = len(result.rows) >= min(body.limit, catalog.max_rows)
    columns = [
        ColumnSpec(name=c, dtype=_dtype(result.rows, i))
        for i, c in enumerate(result.columns)
    ]
    return MetricResult(
        columns=columns,
        rows=result.rows,
        sql=built.sql,
        row_count=len(result.rows),
        truncated=truncated,
    )


def _to_selection(body: MetricQuery) -> MetricSelection:
    filters: list[Filter] = []
    if body.time_range is not None:
        filters.append(
            Filter(
                dimension="date",
                op="between",
                values=[body.time_range.start, body.time_range.end],
            )
        )
    for dim, value in body.filters.items():
        values = value if isinstance(value, list) else [value]
        filters.append(Filter(dimension=dim, op="in", values=values))

    grain = body.time_range.grain if body.time_range else None
    return MetricSelection(
        metric=body.metric,
        dimensions=body.dimensions,
        time_grain=grain,
        filters=filters,
        order_by_metric=body.order,
        limit=body.limit,
    )


def _dtype(rows: list[list[Any]], col: int) -> str:
    for row in rows:
        v = row[col]
        if v is None:
            continue
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, int):
            return "integer"
        if isinstance(v, float):
            return "number"
        return "string"
    return "string"
