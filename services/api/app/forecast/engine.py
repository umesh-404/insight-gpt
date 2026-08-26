"""Forecasting over the governed semantic layer.

The stance of this module is that **a forecast is a claim about the future, and
this system is only allowed to make claims it can support**. Concretely:

1. History is read through the existing catalog + deterministic query builder +
   read-only warehouse executor — the same governed path the dashboards use. No
   free SQL, no ungoverned metrics, no columns outside the allow-list.
2. Below ``min_history`` periods the engine **refuses**: it returns a result with
   an empty ``forecast`` list, ``confidence="none"`` and a caveat saying exactly
   how many periods it has and how many it needs. On the demo warehouse, which
   holds two quarters, that refusal is the correct answer and the one you get.
3. Above the floor it forecasts, but every point carries a prediction interval,
   the method name, ``n_history``, and a ``low_confidence`` flag that stays on
   until the history is genuinely long.
4. Ratio metrics (``additive: false`` in the catalog — margins, rates, averages)
   are forecast **directly from their own period values**. They are never summed
   or averaged across segments, because ratio deltas do not compose.

Method selection is explicit and reported: ``statsforecast`` AutoETS when the
optional extra is installed *and* the history is long enough to justify model
selection, otherwise the stdlib-only damped Holt + additive-season fallback in
:mod:`.smoothing`. The chosen method's name is on the result either way.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..semantic.catalog import CatalogError, Metric, SemanticCatalog
from ..semantic.query_builder import Filter, MetricSelection, build_query
from ..warehouse.executor import Warehouse
from . import smoothing
from .backends import statsforecast_available, statsforecast_project
from .models import (
    Confidence,
    ForecastabilityReport,
    ForecastPoint,
    ForecastResult,
    HistoryPoint,
    MethodFamily,
    MetricForecastability,
)
from .periods import future_periods, missing_periods, season_length

#: The pure-Python method's name, used in results and in the capability report.
FALLBACK_METHOD = "damped Holt trend (pure-Python)"
FALLBACK_METHOD_SEASONAL = "damped Holt trend + additive season (pure-Python)"


@dataclass(slots=True)
class ForecastConfig:
    """Tunable forecasting parameters, with defaults chosen to be cautious."""

    grain: str = "quarter"
    horizon: int = 1
    interval_level: float = 0.80  # 80% prediction interval
    min_history: int = 4  # below this the engine refuses outright
    min_history_statsforecast: int = 8  # model selection needs real data
    medium_history: int = 8
    high_history: int = 16
    max_horizon: int = 12


class ForecastError(ValueError):
    """A caller-fixable forecasting request problem (bad grain, bad horizon)."""


# --------------------------------------------------------------------------
# governed history reads
# --------------------------------------------------------------------------


def fetch_history(
    metric: str,
    catalog: SemanticCatalog,
    warehouse: Warehouse,
    grain: str,
    filters: list[Filter] | None = None,
) -> list[HistoryPoint]:
    """The metric per period, oldest -> newest, via the governed query builder."""
    selection = MetricSelection(
        metric=metric,
        dimensions=["date"],
        time_grain=grain,
        filters=list(filters or []),
    )
    built = build_query(selection, catalog)
    result = warehouse.run(built.sql, built.params)
    points = [
        HistoryPoint(period=str(row[0]), value=float(row[1]))
        for row in result.rows
        if row[0] is not None and row[1] is not None
    ]
    points.sort(key=lambda p: p.period)
    return points


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------


def forecast_metric(
    metric_name: str,
    catalog: SemanticCatalog,
    warehouse: Warehouse,
    *,
    grain: str | None = None,
    horizon: int | None = None,
    filters: list[Filter] | None = None,
    config: ForecastConfig | None = None,
) -> ForecastResult:
    """Forecast one governed metric, refusing when the history cannot support it.

    Raises :class:`~app.semantic.catalog.CatalogError` for an ungoverned metric
    or dimension and :class:`ForecastError` for an invalid grain or horizon —
    both of which the router turns into a 400. Warehouse failures propagate so
    the router can distinguish "unreachable" (503) from "rejected" (400).
    """
    cfg = config or ForecastConfig()
    grain = grain or cfg.grain
    horizon = cfg.horizon if horizon is None else horizon

    metric = catalog.resolve_metric(metric_name)  # CatalogError if ungoverned
    _validate_grain(catalog, grain)
    if not 1 <= horizon <= cfg.max_horizon:
        raise ForecastError(
            f"horizon must be between 1 and {cfg.max_horizon}, got {horizon}"
        )

    history = fetch_history(metric.name, catalog, warehouse, grain, filters)
    return build_result(metric, history, grain, horizon, cfg)


def forecastability(
    catalog: SemanticCatalog,
    warehouse: Warehouse,
    *,
    grain: str | None = None,
    config: ForecastConfig | None = None,
) -> ForecastabilityReport:
    """Which governed metrics can be forecast at ``grain`` right now, and why not."""
    cfg = config or ForecastConfig()
    grain = grain or cfg.grain
    _validate_grain(catalog, grain)

    rows: list[MetricForecastability] = []
    for metric in catalog.metrics.values():
        n, reason, ok = _probe(metric, catalog, warehouse, grain, cfg)
        rows.append(
            MetricForecastability(
                metric=metric.name,
                label=metric.label,
                format=metric.format,
                additive=metric.additive,
                grain=grain,
                n_history=n,
                forecastable=ok,
                reason=reason,
            )
        )
    rows.sort(key=lambda r: (not r.forecastable, r.metric))
    family, method = _advertised_method(cfg)
    return ForecastabilityReport(
        grain=grain,
        min_history=cfg.min_history,
        method_family=family,
        method=method,
        metrics=rows,
    )


def _probe(
    metric: Metric,
    catalog: SemanticCatalog,
    warehouse: Warehouse,
    grain: str,
    cfg: ForecastConfig,
) -> tuple[int, str, bool]:
    if "date" not in metric.dimensions:
        return 0, "Not a time series: the catalog does not allow this metric by date.", False
    history = fetch_history(metric.name, catalog, warehouse, grain, None)
    n = len(history)
    if n < cfg.min_history:
        return (
            n,
            f"Not enough history: {n} {grain}(s) available, {cfg.min_history} required "
            "for a forecast this system is willing to stand behind.",
            False,
        )
    note = "Forecastable."
    if not metric.additive:
        note += " Ratio metric — forecast directly from its own period values, never summed."
    if n < cfg.medium_history:
        note += f" History is short ({n} periods), so any forecast is low-confidence."
    return n, note, True


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------


def build_result(
    metric: Metric,
    history: list[HistoryPoint],
    grain: str,
    horizon: int,
    cfg: ForecastConfig,
) -> ForecastResult:
    """Turn an observed history into a forecast — or into an honest refusal."""
    values = [p.value for p in history]
    n = len(values)
    caveats = _base_caveats(metric, history, grain, n, cfg)

    if n < cfg.min_history:
        return _refusal(metric, history, grain, horizon, cfg, caveats)

    m = season_length(grain)
    points, method, family, seasonal_used = _project(values, horizon, m, cfg)

    if not seasonal_used and m > 1:
        caveats.append(
            f"No seasonal term: {grain} data needs {2 * m} periods to estimate a "
            f"{m}-period cycle and only {n} are available."
        )

    labels = future_periods(history[-1].period, grain, horizon)
    forecast = [
        _point(label, value, low, high, metric, values)
        for label, (value, low, high) in zip(labels, points, strict=True)
    ]

    confidence = _confidence(n, horizon, cfg)
    if confidence in ("none", "low"):
        caveats.append(
            "Low confidence: treat these numbers as a direction of travel, not a plan."
        )
    if horizon > max(1, n // 2):
        caveats.append(
            f"Horizon ({horizon}) is long relative to the history ({n} periods); "
            "the later periods are extrapolation."
        )
    if all(v == values[0] for v in values):
        caveats.append(
            "History is perfectly constant, so the model has no variation to learn "
            "from and the interval collapses to zero width — that is a property of "
            "the input, not evidence of certainty."
        )

    return ForecastResult(
        metric=metric.name,
        metric_label=metric.label,
        format=metric.format,
        additive=metric.additive,
        grain=grain,
        horizon=horizon,
        history=history,
        forecast=forecast,
        method=method,
        method_family=family,
        n_history=n,
        interval_level=cfg.interval_level,
        confidence=confidence,
        low_confidence=confidence in ("none", "low"),
        caveats=caveats,
        headline=_headline(metric, forecast, confidence, n, grain, method, cfg),
    )


def _project(
    values: list[float], horizon: int, m: int, cfg: ForecastConfig
) -> tuple[list[tuple[float, float, float]], str, MethodFamily, bool]:
    """Pick and run a method; always returns something usable."""
    if statsforecast_available() and len(values) >= cfg.min_history_statsforecast:
        out = statsforecast_project(values, horizon, m, cfg.interval_level)
        if out is not None:
            points, name = out
            return points, name, "statsforecast", m > 1 and len(values) >= 2 * m
    points, _fit, seasonal_used = smoothing.project(values, horizon, m, cfg.interval_level)
    method = FALLBACK_METHOD_SEASONAL if seasonal_used else FALLBACK_METHOD
    return points, method, "fallback", seasonal_used


def _point(
    period: str,
    value: float,
    lower: float,
    upper: float,
    metric: Metric,
    history: list[float],
) -> ForecastPoint:
    """Clamp to what the metric can physically be, then order the bounds."""
    if metric.format == "percent" and all(0.0 <= v <= 1.0 for v in history):
        value, lower, upper = (min(max(x, 0.0), 1.0) for x in (value, lower, upper))
    elif metric.format == "integer" and all(v >= 0 for v in history):
        value, lower, upper = (max(x, 0.0) for x in (value, lower, upper))
    return ForecastPoint(
        period=period,
        value=round(value, 6),
        lower=round(min(lower, value), 6),
        upper=round(max(upper, value), 6),
    )


def _refusal(
    metric: Metric,
    history: list[HistoryPoint],
    grain: str,
    horizon: int,
    cfg: ForecastConfig,
    caveats: list[str],
) -> ForecastResult:
    """No numbers at all — just the reason there are none."""
    n = len(history)
    caveats.insert(
        0,
        f"Refused to forecast: {n} {grain}(s) of history, {cfg.min_history} required. "
        "A projection from this little data would be a guess dressed as an estimate.",
    )
    family, _ = _advertised_method(cfg)
    return ForecastResult(
        metric=metric.name,
        metric_label=metric.label,
        format=metric.format,
        additive=metric.additive,
        grain=grain,
        horizon=horizon,
        history=history,
        forecast=[],
        method="none — insufficient history",
        method_family="none",
        n_history=n,
        interval_level=cfg.interval_level,
        confidence="none",
        low_confidence=True,
        caveats=caveats,
        headline=(
            f"Not enough history to forecast {metric.label} at {grain} grain: "
            f"{n} period(s) available, {cfg.min_history} required. "
            f"(Available engine: {family}.)"
        ),
    )


def _base_caveats(
    metric: Metric, history: list[HistoryPoint], grain: str, n: int, cfg: ForecastConfig
) -> list[str]:
    caveats: list[str] = []
    if not metric.additive:
        caveats.append(
            f"{metric.label} is a ratio metric: it is forecast directly from its own "
            "period values. Segment forecasts of a ratio are never summed."
        )
    gaps = missing_periods([p.period for p in history], grain)
    if gaps:
        shown = ", ".join(gaps[:5]) + ("…" if len(gaps) > 5 else "")
        caveats.append(
            f"History has {len(gaps)} missing {grain}(s) ({shown}); the model treats "
            "the remaining points as evenly spaced, which they are not."
        )
    if 0 < n < cfg.min_history:
        caveats.append(f"Only {n} period(s) of history exist at {grain} grain.")
    if n == 0:
        caveats.append(
            f"The warehouse returned no rows for {metric.label} at {grain} grain."
        )
    return caveats


def _confidence(n: int, horizon: int, cfg: ForecastConfig) -> Confidence:
    if n < cfg.min_history:
        return "none"
    grade: Confidence = "low"
    if n >= cfg.high_history:
        grade = "high"
    elif n >= cfg.medium_history:
        grade = "medium"
    # A horizon that outruns the history cannot be better than low confidence.
    if horizon > max(1, n // 2):
        grade = "low"
    return grade


def _advertised_method(cfg: ForecastConfig) -> tuple[MethodFamily, str]:
    if statsforecast_available():
        return "statsforecast", (
            f"statsforecast AutoETS (from {cfg.min_history_statsforecast} periods), "
            f"else {FALLBACK_METHOD}"
        )
    return "fallback", FALLBACK_METHOD


def _validate_grain(catalog: SemanticCatalog, grain: str) -> None:
    date_dim = catalog.dimensions.get("date")
    if date_dim is None or not date_dim.is_date():
        raise ForecastError("the catalog has no governed date dimension to forecast over")
    if grain not in date_dim.grains:
        raise CatalogError(
            f"unknown grain {grain!r}; governed grains: {sorted(date_dim.grains)}"
        )


def _headline(
    metric: Metric,
    forecast: list[ForecastPoint],
    confidence: Confidence,
    n: int,
    grain: str,
    method: str,
    cfg: ForecastConfig,
) -> str:
    first = forecast[0]
    pct = int(round(cfg.interval_level * 100))
    text = (
        f"{metric.label} is projected at {_fmt(first.value, metric.format)} for "
        f"{first.period}, with an {pct}% interval of "
        f"{_fmt(first.lower, metric.format)} to {_fmt(first.upper, metric.format)}. "
        f"Fit on {n} {grain}(s) of history using {method}."
    )
    if confidence in ("none", "low"):
        text += " Confidence is low — this is an estimate, not a commitment."
    return text


def _fmt(value: float, fmt: str) -> str:
    if fmt == "currency":
        sign = "-" if value < 0 else ""
        v = abs(value)
        if v >= 1_000_000:
            return f"{sign}${v / 1_000_000:.2f}M"
        if v >= 1_000:
            return f"{sign}${v / 1_000:.1f}K"
        return f"{sign}${v:,.0f}"
    if fmt == "percent":
        return f"{value * 100:.1f}%"
    if fmt == "integer":
        return f"{value:,.0f}"
    return f"{value:,.2f}"
