"""Governed metrics: the semantic catalog and the direct (non-LLM) query path.

``GET /metrics`` publishes the *full* governed catalog (doc 06 §3.2) — metric
keys, labels, units, display formats, allowed grouping dimensions and the date
grains — so the dashboard can render labels and units without hardcoding them.
It is the allow-list for ``POST /metrics/query``.

The query endpoint (analyst+) accepts a governed selection (metric, dimensions,
filters, grain, ordering, limit), compiles it through the existing
``query_builder`` + guardrails, and executes it read-only against whichever
warehouse is configured (DuckDB fixture or Postgres). It never accepts SQL, and
every governance failure surfaces as a clean ``400``, never a ``500``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...engine.guardrails import GuardrailError
from ...semantic.catalog import CatalogError, Metric, SemanticCatalog
from ...semantic.query_builder import Filter, MetricSelection, build_query
from ...warehouse.executor import Warehouse
from ..deps import get_catalog, get_warehouse, rate_limit
from ..errors import BadRequestError, DependencyUnavailableError

router = APIRouter(tags=["metrics"])

Direction = Literal["asc", "desc"]

# catalog ``format`` -> the unit vocabulary the frontend expects (doc 06 §3.2).
_UNIT = {
    "currency": "currency",
    "integer": "count",
    "number": "count",
    "percent": "ratio",
    "duration": "duration",
}

# Leading aggregate in a metric expression -> the ``default_agg`` vocabulary.
_AGG_PREFIX = (
    ("SUM(", "sum"),
    ("COUNT(", "count"),
    ("AVG(", "avg"),
    ("MIN(", "min"),
    ("MAX(", "max"),
)


class MetricDef(BaseModel):
    key: str
    label: str
    description: str
    unit: str
    format: str                       # raw catalog format: currency|percent|integer|number
    grain: list[str]                  # dimensions this metric may be grouped by
    default_agg: str
    additive: bool = True
    aliases: list[str] = Field(default_factory=list)


class DimensionDef(BaseModel):
    key: str
    label: str
    grains: list[str] = Field(default_factory=list)
    is_date: bool = False
    default_grain: str | None = None


class CatalogLimits(BaseModel):
    max_rows: int
    default_rows: int
    statement_timeout_ms: int


class MetricsCatalog(BaseModel):
    metrics: list[MetricDef]
    dimensions: list[DimensionDef]
    time_grains: list[str] = Field(default_factory=list)
    limits: CatalogLimits | None = None


class TimeRange(BaseModel):
    grain: str | None = None
    start: str
    end: str


class MetricFilter(BaseModel):
    """An explicit governed filter — the list form the dashboards send."""

    dimension: str
    op: Literal["in", "between", "eq"] = "in"
    values: list[str | int | float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> MetricFilter:
        if not self.values:
            raise ValueError(f"filter on {self.dimension!r} needs at least one value")
        if self.op == "between" and len(self.values) != 2:
            raise ValueError("'between' needs exactly two values [start, end]")
        if self.op == "eq" and len(self.values) != 1:
            raise ValueError("'eq' needs exactly one value")
        return self


class MetricQuery(BaseModel):
    """A governed selection. Never SQL.

    ``filters`` accepts either the compact mapping form
    (``{"region": "North"}``) or the explicit list form
    (``[{"dimension": "date", "op": "between", "values": [...]}]``).
    Ordering may be given as ``order_by_metric`` (canonical), ``order``, or
    ``order_by`` — all mean "order by the metric value".
    """

    metric: str = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, Any] | list[MetricFilter] = Field(default_factory=dict)
    time_range: TimeRange | None = None
    time_grain: str | None = None
    order_by_metric: Direction | None = None
    order: Direction | None = None
    order_by: str | None = None
    limit: int = Field(default=1000, le=10000, ge=1)

    def direction(self) -> Direction | None:
        """The single effective sort direction, whichever spelling was used."""
        for value in (self.order_by_metric, self.order):
            if value in ("asc", "desc"):
                return value
        raw = (self.order_by or "").strip().lower()
        if not raw:
            return None
        if raw.startswith("-"):
            return "desc"
        if raw.endswith("desc"):
            return "desc"
        if raw in ("asc", "ascending") or raw.endswith("asc"):
            return "asc"
        return None

    def grain(self) -> str | None:
        """Explicit ``time_grain`` wins; otherwise the time range's grain."""
        return self.time_grain or (self.time_range.grain if self.time_range else None)


class ColumnSpec(BaseModel):
    name: str
    dtype: str
    role: Literal["dimension", "metric"] = "dimension"


class ResultMeta(BaseModel):
    metric: str
    label: str
    unit: str
    format: str
    dimensions: list[str] = Field(default_factory=list)
    time_grain: str | None = None
    order: Direction | None = None
    limit: int


class MetricResult(BaseModel):
    columns: list[ColumnSpec]
    rows: list[list[Any]]
    records: list[dict[str, Any]] = Field(default_factory=list)
    sql: str
    row_count: int
    truncated: bool
    meta: ResultMeta | None = None


def _default_agg(metric: Metric) -> str:
    if not metric.additive:
        return "ratio"
    expr = metric.expr.strip().upper()
    for prefix, agg in _AGG_PREFIX:
        if expr.startswith(prefix):
            return agg
    return "sum"


def _describe(metric: Metric) -> str:
    unit = _UNIT.get(metric.format, "count")
    dims = ", ".join(sorted(metric.dimensions)) or "no dimensions"
    return f"{metric.label} ({unit}) over {metric.fact}; groupable by {dims}."


def _dimension_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


@router.get("/metrics", response_model=MetricsCatalog)
async def list_metrics(
    _: object = Depends(require_role(Role.viewer)),
    catalog: SemanticCatalog = Depends(get_catalog),
) -> MetricsCatalog:
    metrics = [
        MetricDef(
            key=m.name,
            label=m.label,
            description=_describe(m),
            unit=_UNIT.get(m.format, "count"),
            format=m.format,
            grain=list(m.dimensions),
            default_agg=_default_agg(m),
            additive=m.additive,
            aliases=list(m.aliases),
        )
        for m in catalog.metrics.values()
    ]
    dimensions = [
        DimensionDef(
            key=d.name,
            label=_dimension_label(d.name),
            grains=sorted(d.grains),
            is_date=d.is_date(),
            default_grain=d.default_grain,
        )
        for d in catalog.dimensions.values()
    ]
    time_grains = sorted(
        {g for d in catalog.dimensions.values() if d.is_date() for g in d.grains}
    )
    return MetricsCatalog(
        metrics=metrics,
        dimensions=dimensions,
        time_grains=time_grains,
        limits=CatalogLimits(
            max_rows=catalog.max_rows,
            default_rows=catalog.default_rows,
            statement_timeout_ms=catalog.statement_timeout_ms,
        ),
    )


@router.post(
    "/metrics/query",
    response_model=MetricResult,
    dependencies=[Depends(rate_limit("read"))],
)
async def query_metric(
    body: MetricQuery,
    _: object = Depends(require_role(Role.analyst)),
    catalog: SemanticCatalog = Depends(get_catalog),
    warehouse: Warehouse = Depends(get_warehouse),
) -> MetricResult:
    selection = _to_selection(body)
    try:
        built = build_query(selection, catalog)
    except (CatalogError, ValueError) as exc:
        # Governance rejection is the caller's fault, not a server fault.
        raise BadRequestError(str(exc)) from exc

    try:
        # The executors are synchronous; keep the event loop free.
        result = await run_in_threadpool(warehouse.run, built.sql, built.params)
    except (CatalogError, GuardrailError) as exc:
        raise BadRequestError(str(exc)) from exc
    except Exception as exc:  # warehouse unreachable / query rejected downstream
        raise _warehouse_error(exc) from exc

    effective_limit = min(body.limit, catalog.max_rows)
    metric = catalog.resolve_metric(body.metric)
    columns = [
        ColumnSpec(
            name=c,
            dtype=_dtype(result.rows, i),
            role="metric" if c == metric.name else "dimension",
        )
        for i, c in enumerate(result.columns)
    ]
    records = [dict(zip(result.columns, row, strict=False)) for row in result.rows]
    return MetricResult(
        columns=columns,
        rows=result.rows,
        records=records,
        sql=built.sql,
        row_count=len(result.rows),
        truncated=len(result.rows) >= effective_limit,
        meta=ResultMeta(
            metric=metric.name,
            label=metric.label,
            unit=_UNIT.get(metric.format, "count"),
            format=metric.format,
            dimensions=list(selection.dimensions),
            time_grain=selection.time_grain,
            order=body.direction(),
            limit=effective_limit,
        ),
    )


def _warehouse_error(exc: Exception) -> Exception:
    """Map an executor failure onto the right envelope (never a bare 500)."""
    text = str(exc)
    lowered = f"{type(exc).__name__} {text}".lower()
    connectivity = ("connect", "connection", "timeout", "could not translate", "refused")
    if any(term in lowered for term in connectivity):
        return DependencyUnavailableError("The warehouse is not reachable right now.")
    return BadRequestError(f"The warehouse rejected the query: {text}")


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
    if isinstance(body.filters, list):
        for f in body.filters:
            op = "in" if f.op == "eq" else f.op
            filters.append(Filter(dimension=f.dimension, op=op, values=list(f.values)))
    else:
        for dim, value in body.filters.items():
            values = value if isinstance(value, list) else [value]
            if not values:
                raise BadRequestError(f"filter on {dim!r} needs at least one value")
            filters.append(Filter(dimension=dim, op="in", values=values))

    return MetricSelection(
        metric=body.metric,
        dimensions=body.dimensions,
        time_grain=body.grain(),
        filters=filters,
        order_by_metric=body.direction(),
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
        if isinstance(v, float | Decimal):
            return "number"
        return "string"
    return "string"
