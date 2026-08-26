"""Typed records for the proactive insight digest.

An :class:`Insight` is a self-contained, explainable record: the anomalous
metric and its period-over-period movement, the deterministic root-cause segment
(from contribution analysis), the metric's own trend for a sparkline, and any
supporting documents. Every number on it originates from the warehouse; the
prose is templated, never a model's guess at a figure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
Direction = Literal["up", "down"]


class TrendPoint(BaseModel):
    """One (period, value) point of the metric's own history."""

    period: str
    value: float


class ContributionRow(BaseModel):
    """A segment's contribution to the total change, for the detail table."""

    dimension: str
    segment: str
    current: float
    prior: float
    delta: float
    contribution_pct: float  # signed share of the total change, percent


class RootCause(BaseModel):
    """The single segment that best explains the movement."""

    dimension: str
    segment: str
    current: float
    prior: float
    delta: float
    contribution_pct: float


class EvidenceDoc(BaseModel):
    """A supporting document (mirrors the answer envelope's ``Citation``)."""

    n: int
    doc_id: str
    source_type: str
    title: str
    date: str | None = None
    score: float | None = None
    snippet: str | None = None


class Insight(BaseModel):
    """A flagged anomaly with its explanation and evidence."""

    id: str
    metric: str
    metric_label: str
    metric_format: str  # currency | percent | integer | number — display hint
    grain: str  # e.g. "quarter"
    period: str  # current period label, e.g. "2026Q2"
    prior_period: str  # e.g. "2026Q1"
    current: float
    prior: float
    change_abs: float
    change_pct: float  # signed ratio, e.g. -0.114 (frontend-friendly)
    direction: Direction
    severity: Severity
    z_score: float | None = None  # robust z, when enough history exists
    method: str  # honest description of how this was flagged
    headline: str
    root_cause: RootCause | None = None
    contributions: list[ContributionRow] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list)
    evidence: list[EvidenceDoc] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
