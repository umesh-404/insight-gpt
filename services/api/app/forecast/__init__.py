"""Honest forecasting over the governed metrics (see ``docs/13-forecasting.md``).

The engine explains *why a metric changed*; this package projects *what happens
next* — with a prediction interval, a named method, the history size it was fit
on, and an explicit refusal when the history is too short to support a claim.
"""

from .engine import (
    FALLBACK_METHOD,
    ForecastConfig,
    ForecastError,
    fetch_history,
    forecast_metric,
    forecastability,
)
from .models import (
    ForecastabilityReport,
    ForecastPoint,
    ForecastResult,
    HistoryPoint,
    MetricForecastability,
)

__all__ = [
    "FALLBACK_METHOD",
    "ForecastConfig",
    "ForecastError",
    "ForecastPoint",
    "ForecastResult",
    "ForecastabilityReport",
    "HistoryPoint",
    "MetricForecastability",
    "fetch_history",
    "forecast_metric",
    "forecastability",
]
