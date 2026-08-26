"""Forecasting endpoints — projection over the governed semantic layer.

``POST /forecast`` (analyst+) takes a governed metric, a date grain and a
horizon, reads the metric's own history through the deterministic query builder,
and returns a :class:`~app.forecast.models.ForecastResult`: points with
prediction intervals, the method that produced them, how many periods it was fit
on, a confidence grade, and the caveats a reader needs. When the history is too
short the endpoint still returns 200 with an **empty** forecast and an explicit
refusal — silence dressed as a number is the failure mode this design exists to
prevent.

``GET /forecast/metrics`` (viewer+) publishes which governed metrics are
forecastable at a grain and, for the ones that are not, why.

Never SQL, never an ungoverned metric, never a 500: governance rejections are
``400``, an unreachable warehouse is ``503``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...engine.guardrails import GuardrailError
from ...forecast.engine import ForecastConfig, ForecastError, forecast_metric, forecastability
from ...forecast.models import ForecastabilityReport, ForecastResult
from ...semantic.catalog import CatalogError, SemanticCatalog
from ...semantic.query_builder import Filter
from ...warehouse.executor import Warehouse
from ..deps import get_catalog, get_warehouse, rate_limit
from ..errors import BadRequestError, DependencyUnavailableError

router = APIRouter(tags=["forecast"])

_DEFAULTS = ForecastConfig()


class ForecastFilter(BaseModel):
    """An explicit governed filter, mirroring ``POST /metrics/query``."""

    dimension: str
    op: Literal["in", "between", "eq"] = "in"
    values: list[str | int | float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ForecastFilter:
        if not self.values:
            raise ValueError(f"filter on {self.dimension!r} needs at least one value")
        if self.op == "between" and len(self.values) != 2:
            raise ValueError("'between' needs exactly two values [start, end]")
        if self.op == "eq" and len(self.values) != 1:
            raise ValueError("'eq' needs exactly one value")
        return self


class ForecastRequest(BaseModel):
    """A governed forecast request. Never SQL.

    ``filters`` accepts the compact mapping form (``{"region": "North"}``) or the
    explicit list form, exactly like the metrics query endpoint.
    """

    metric: str = Field(min_length=1)
    grain: str = _DEFAULTS.grain
    horizon: int = Field(default=_DEFAULTS.horizon, ge=1, le=_DEFAULTS.max_horizon)
    filters: dict[str, Any] | list[ForecastFilter] = Field(default_factory=dict)
    interval_level: float = Field(default=_DEFAULTS.interval_level, ge=0.5, le=0.99)


@router.get("/forecast/metrics", response_model=ForecastabilityReport)
async def list_forecastable_metrics(
    grain: str = Query(default=_DEFAULTS.grain),
    _: object = Depends(require_role(Role.viewer)),
    catalog: SemanticCatalog = Depends(get_catalog),
    warehouse: Warehouse = Depends(get_warehouse),
) -> ForecastabilityReport:
    try:
        return await run_in_threadpool(
            forecastability, catalog, warehouse, grain=grain, config=_DEFAULTS
        )
    except (CatalogError, ForecastError, GuardrailError, ValueError) as exc:
        raise BadRequestError(str(exc)) from exc
    except Exception as exc:  # warehouse unreachable — never a bare 500
        raise _warehouse_error(exc) from exc


@router.post(
    "/forecast",
    response_model=ForecastResult,
    dependencies=[Depends(rate_limit("read"))],
)
async def create_forecast(
    body: ForecastRequest,
    _: object = Depends(require_role(Role.analyst)),
    catalog: SemanticCatalog = Depends(get_catalog),
    warehouse: Warehouse = Depends(get_warehouse),
) -> ForecastResult:
    config = ForecastConfig(
        grain=body.grain,
        horizon=body.horizon,
        interval_level=body.interval_level,
    )
    try:
        filters = _to_filters(body)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    try:
        # Fitting and the warehouse read are both synchronous and CPU/IO bound;
        # keep the event loop free.
        return await run_in_threadpool(
            forecast_metric,
            body.metric,
            catalog,
            warehouse,
            grain=body.grain,
            horizon=body.horizon,
            filters=filters,
            config=config,
        )
    except (CatalogError, ForecastError, GuardrailError, ValueError) as exc:
        # An ungoverned metric, an unknown grain, or a rejected filter is the
        # caller's mistake, not a server fault.
        raise BadRequestError(str(exc)) from exc
    except Exception as exc:
        raise _warehouse_error(exc) from exc


def _to_filters(body: ForecastRequest) -> list[Filter]:
    filters: list[Filter] = []
    if isinstance(body.filters, list):
        for f in body.filters:
            op = "in" if f.op == "eq" else f.op
            filters.append(Filter(dimension=f.dimension, op=op, values=list(f.values)))
        return filters
    for dimension, value in body.filters.items():
        values = value if isinstance(value, list) else [value]
        if not values:
            raise ValueError(f"filter on {dimension!r} needs at least one value")
        filters.append(Filter(dimension=dimension, op="in", values=values))
    return filters


def _warehouse_error(exc: Exception) -> Exception:
    """Map an executor failure onto the right envelope (never a bare 500)."""
    text = str(exc)
    lowered = f"{type(exc).__name__} {text}".lower()
    connectivity = ("connect", "connection", "timeout", "could not translate", "refused")
    if any(term in lowered for term in connectivity):
        return DependencyUnavailableError("The warehouse is not reachable right now.")
    return BadRequestError(f"The warehouse rejected the query: {text}")
