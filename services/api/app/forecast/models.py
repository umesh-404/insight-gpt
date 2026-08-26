"""Typed records for a metric forecast.

A :class:`ForecastResult` is deliberately self-incriminating: alongside the
projected values it always carries the method that produced them, how many
historical points that method was fit on, a prediction interval per point, a
coarse ``confidence`` grade, an explicit ``low_confidence`` flag, and the list of
``caveats`` that a reader must see before trusting the numbers.

When there is not enough history to project honestly, ``forecast`` is **empty**
and ``confidence`` is ``"none"`` — the result still returns 200 and explains
itself. A forecast is never presented as fact.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Coarse trust grade. ``"none"`` means *no forecast was produced*.
Confidence = Literal["none", "low", "medium", "high"]

#: Which family produced the numbers, for clients that want to badge it.
MethodFamily = Literal["statsforecast", "fallback", "none"]


class HistoryPoint(BaseModel):
    """One observed (period, value) point pulled from the governed layer."""

    period: str
    value: float


class ForecastPoint(BaseModel):
    """One projected period with its prediction interval.

    ``lower <= value <= upper`` always holds; on a perfectly constant history
    the interval collapses to zero width rather than exploding.
    """

    period: str
    value: float
    lower: float
    upper: float


class ForecastResult(BaseModel):
    """A projection of one governed metric, with its own uncertainty attached."""

    metric: str
    metric_label: str
    format: str  # currency | percent | integer | number — display hint
    additive: bool
    grain: str
    horizon: int

    history: list[HistoryPoint] = Field(default_factory=list)
    forecast: list[ForecastPoint] = Field(default_factory=list)

    method: str  # human-readable, e.g. "damped Holt trend (pure-Python)"
    method_family: MethodFamily
    n_history: int
    interval_level: float  # e.g. 0.8 -> an 80% prediction interval

    confidence: Confidence
    low_confidence: bool  # true whenever confidence is "none" or "low"
    caveats: list[str] = Field(default_factory=list)
    headline: str


class MetricForecastability(BaseModel):
    """Whether one governed metric can be forecast right now, and why not."""

    metric: str
    label: str
    format: str
    additive: bool
    grain: str
    n_history: int
    forecastable: bool
    reason: str


class ForecastabilityReport(BaseModel):
    """The catalog-wide answer to "what can this system actually forecast?"."""

    grain: str
    min_history: int
    method_family: MethodFamily
    method: str
    metrics: list[MetricForecastability] = Field(default_factory=list)
