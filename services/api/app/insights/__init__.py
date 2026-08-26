"""Proactive insight digest: anomaly detection + root cause over governed metrics.

This package is the shared core behind two surfaces:

* the API's ``/api/v1/insights`` router (on-demand generation over the fixture
  warehouse, or reads from the persisted table), and
* the worker's ``insight_digest`` scheduled job (periodic generation +
  persistence).

Both import :func:`detect_insights` and :class:`InsightStore` from here so the
detection logic lives in exactly one place. Nothing in here is model magic: the
method is a documented, deterministic period-over-period rule (see
:func:`detect_insights`), and the *where* of a change reuses the engine's own
:func:`app.engine.contribution.contribution` template — no invented numbers.
"""

from __future__ import annotations

from .detector import DetectionConfig, detect_insights
from .models import ContributionRow, EvidenceDoc, Insight, RootCause, TrendPoint
from .store import InsightStore

__all__ = [
    "ContributionRow",
    "DetectionConfig",
    "EvidenceDoc",
    "Insight",
    "InsightStore",
    "RootCause",
    "TrendPoint",
    "detect_insights",
]
