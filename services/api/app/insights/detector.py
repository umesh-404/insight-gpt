"""Anomaly detection over the governed semantic layer.

Detection method (deliberately simple and honest — this is a robust statistical
rule, **not** machine learning):

1. For each governed metric, compute its value at a chosen grain (default
   ``quarter``) for every period present in the warehouse, using the existing
   deterministic query builder — no free SQL. The most recent period is the
   *current*, the one before it the *prior*.
2. Flag the metric when the period-over-period change clears **both** a relative
   threshold (``|change_pct| >= pct_threshold``) **and** a minimum absolute
   magnitude (``|change_abs| >= min_abs``), so tiny noise on a small base is not
   reported. When at least ``min_history`` periods exist, a robust
   median/MAD **z-score** of the current value against the metric's own history
   is also computed and can raise severity; on the 2-period demo warehouse there
   is not enough history for a z-score, so only the threshold rule fires.
3. For a flagged **additive** metric, reuse the engine's
   :func:`app.engine.contribution.contribution` template to break the change
   down by each candidate dimension (region / category / product) and pick the
   single segment that most explains it as the root cause. Non-additive ratio
   metrics (margins, rates, averages) are still flagged, but carry no
   contribution breakdown because segment deltas of a ratio do not sum.
4. Optionally attach a few supporting documents from the engine's retriever,
   scoped to the root-cause segment.

Everything is grounded: the only numbers on an :class:`Insight` come from the
warehouse; prose is templated around them.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from ..engine.contribution import contribution
from ..engine.engine import InsightEngine
from ..engine.retrieval import Retriever
from ..formatting import format_value as _fmt
from ..semantic.catalog import Metric, SemanticCatalog
from ..semantic.query_builder import MetricSelection, build_query
from ..warehouse.executor import Warehouse
from .models import (
    ContributionRow,
    Direction,
    EvidenceDoc,
    Insight,
    RootCause,
    Severity,
    TrendPoint,
)

# Dimensions we try, in priority order, when attributing a change to a segment.
_CANDIDATE_DIMS = ("region", "category", "product")

# Metrics whose values are ratios/averages: never sum segment deltas for these.
# (Detected from the catalog's ``additive`` flag; this is just documentation.)


@dataclass(slots=True)
class DetectionConfig:
    """Tunable detection parameters (sane, documented defaults)."""

    grain: str = "quarter"
    pct_threshold: float = 0.05  # 5% period-over-period
    min_abs: float = 0.0  # minimum absolute change; per-deployment floor
    high_pct: float = 0.10  # |change_pct| at/above this -> high severity
    medium_pct: float = 0.05  # ...-> medium severity
    high_z: float = 3.0  # robust z at/above this -> high severity
    min_history: int = 4  # periods needed before a z-score is meaningful
    max_contrib_rows: int = 8  # cap the detail table per dimension
    evidence_k: int = 3  # supporting docs to attach


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def detect_insights(
    engine: InsightEngine, config: DetectionConfig | None = None
) -> list[Insight]:
    """Run detection over every governed metric; return flagged insights.

    Newest/most-severe first. Reuses ``engine``'s catalog, warehouse and
    retriever, so the same call works over the offline fixture stack or a live
    Postgres warehouse. Never raises for a single bad metric — it is skipped and
    the rest still run.
    """
    cfg = config or DetectionConfig()
    insights: list[Insight] = []
    for metric in engine.catalog.metrics.values():
        try:
            insight = _detect_for_metric(metric, engine, cfg)
        except Exception:  # noqa: BLE001 — one bad metric must not sink the digest
            continue
        if insight is not None:
            insights.append(insight)
    insights.sort(key=lambda i: (_severity_rank(i.severity), abs(i.change_pct)), reverse=True)
    return insights


# --------------------------------------------------------------------------
# per-metric detection
# --------------------------------------------------------------------------


def _detect_for_metric(
    metric: Metric, engine: InsightEngine, cfg: DetectionConfig
) -> Insight | None:
    catalog = engine.catalog
    warehouse = engine.warehouse

    trend = _metric_by_period(metric.name, catalog, warehouse, cfg.grain)
    if len(trend) < 2:
        return None  # need a prior period to compare against

    current, prior = trend[-1], trend[-2]
    if prior.value == 0:
        return None  # relative change is undefined on a zero base
    change_abs = current.value - prior.value
    change_pct = change_abs / abs(prior.value)

    if abs(change_pct) < cfg.pct_threshold or abs(change_abs) < cfg.min_abs:
        return None

    z = _robust_z([p.value for p in trend], cfg.min_history)
    direction: Direction = "down" if change_abs < 0 else "up"
    severity = _severity(change_pct, z, cfg)

    root_cause, contributions = (None, [])
    if metric.additive:
        root_cause, contributions = _root_cause(
            metric, catalog, warehouse, current.period, prior.period, direction, cfg
        )

    evidence = _evidence(metric, engine.retriever, root_cause, current.period, cfg)

    return Insight(
        id=f"ins_{metric.name}_{current.period}".lower().replace(" ", "_"),
        metric=metric.name,
        metric_label=metric.label,
        metric_format=metric.format,
        grain=cfg.grain,
        period=current.period,
        prior_period=prior.period,
        current=current.value,
        prior=prior.value,
        change_abs=change_abs,
        change_pct=round(change_pct, 4),
        direction=direction,
        severity=severity,
        z_score=(round(z, 2) if z is not None else None),
        method=_method_text(cfg, z),
        headline=_headline(metric, current, prior, change_pct, direction, root_cause),
        root_cause=root_cause,
        contributions=contributions,
        trend=trend,
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# warehouse reads (governed selections only — no free SQL)
# --------------------------------------------------------------------------


def _metric_by_period(
    metric: str, catalog: SemanticCatalog, warehouse: Warehouse, grain: str
) -> list[TrendPoint]:
    """The metric totalled per period, ordered oldest -> newest."""
    selection = MetricSelection(metric=metric, dimensions=["date"], time_grain=grain)
    built = build_query(selection, catalog)
    res = warehouse.run(built.sql, built.params)
    points = [
        TrendPoint(period=str(r[0]), value=float(r[1]))
        for r in res.rows
        if r[0] is not None and r[1] is not None
    ]
    points.sort(key=lambda p: p.period)
    return points


def _metric_by_period_dim(
    metric: str, dim: str, catalog: SemanticCatalog, warehouse: Warehouse, grain: str
) -> dict[str, dict[str, float]]:
    """The metric per (period, segment): ``{period: {segment: value}}``."""
    selection = MetricSelection(metric=metric, dimensions=["date", dim], time_grain=grain)
    built = build_query(selection, catalog)
    res = warehouse.run(built.sql, built.params)
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for row in res.rows:
        if row[0] is None or row[1] is None or row[2] is None:
            continue
        out[str(row[0])][str(row[1])] = float(row[2])
    return out


# --------------------------------------------------------------------------
# root cause via the existing contribution template
# --------------------------------------------------------------------------


def _root_cause(
    metric: Metric,
    catalog: SemanticCatalog,
    warehouse: Warehouse,
    current_period: str,
    prior_period: str,
    direction: Direction,
    cfg: DetectionConfig,
) -> tuple[RootCause | None, list[ContributionRow]]:
    all_rows: list[ContributionRow] = []
    best: RootCause | None = None

    for dim in _CANDIDATE_DIMS:
        if dim not in metric.dimensions:
            continue
        by_period = _metric_by_period_dim(metric.name, dim, catalog, warehouse, cfg.grain)
        cur_map = by_period.get(current_period, {})
        prior_map = by_period.get(prior_period, {})
        if not cur_map and not prior_map:
            continue

        deltas = contribution(cur_map, prior_map)
        for seg in deltas[: cfg.max_contrib_rows]:
            all_rows.append(
                ContributionRow(
                    dimension=dim,
                    segment=seg.label,
                    current=seg.current,
                    prior=seg.prior,
                    delta=seg.delta,
                    contribution_pct=seg.contribution_pct,
                )
            )
        # The root cause is the segment whose delta matches the overall
        # direction and has the largest magnitude across candidate dimensions.
        for seg in deltas:
            if direction == "down" and seg.delta >= 0:
                continue
            if direction == "up" and seg.delta <= 0:
                continue
            if best is None or abs(seg.delta) > abs(best.delta):
                best = RootCause(
                    dimension=dim,
                    segment=seg.label,
                    current=seg.current,
                    prior=seg.prior,
                    delta=seg.delta,
                    contribution_pct=seg.contribution_pct,
                )
            break  # deltas are sorted; the first matching row is the extreme one

    return best, all_rows


# --------------------------------------------------------------------------
# supporting documents
# --------------------------------------------------------------------------


def _evidence(
    metric: Metric,
    retriever: Retriever,
    root_cause: RootCause | None,
    period: str,
    cfg: DetectionConfig,
) -> list[EvidenceDoc]:
    if root_cause is None:
        return []
    # Scope the search to the root-cause segment — the explanation lives in
    # documents about it, not in the metric's name (doc 05 §3.3).
    query = f"{metric.label} {root_cause.segment} complaints issues delays backlog"
    filters: dict = {}
    if root_cause.dimension in ("region", "category"):
        filters[root_cause.dimension] = [root_cause.segment]
    bounds = _quarter_bounds(period)
    if bounds:
        filters["date_range"] = {"start": bounds[0], "end": bounds[1]}

    try:
        docs = retriever.search(query, filters=filters, k=cfg.evidence_k)
    except Exception:  # noqa: BLE001 — evidence is best-effort, never fatal
        return []
    return [
        EvidenceDoc(
            n=i + 1,
            doc_id=d.doc_id,
            source_type=d.source_type,
            title=d.title,
            date=d.date,
            score=d.score,
            snippet=(d.body[:200] if getattr(d, "body", None) else None),
        )
        for i, d in enumerate(docs)
    ]


# --------------------------------------------------------------------------
# scoring + prose
# --------------------------------------------------------------------------


def _robust_z(values: list[float], min_history: int) -> float | None:
    """Median/MAD z-score of the latest value against its history.

    Robust to outliers (unlike a mean/stdev z). Returns ``None`` when there is
    too little history for the score to mean anything.
    """
    if len(values) < min_history:
        return None
    history = values[:-1]
    median = statistics.median(history)
    mad = statistics.median([abs(v - median) for v in history])
    if mad == 0:
        return None
    # 1.4826 scales MAD to a stddev-equivalent for normal-ish data.
    return (values[-1] - median) / (1.4826 * mad)


def _severity(change_pct: float, z: float | None, cfg: DetectionConfig) -> Severity:
    if abs(change_pct) >= cfg.high_pct or (z is not None and abs(z) >= cfg.high_z):
        return "high"
    if abs(change_pct) >= cfg.medium_pct:
        return "medium"
    return "low"


def _severity_rank(sev: Severity) -> int:
    return {"high": 3, "medium": 2, "low": 1}[sev]


def _method_text(cfg: DetectionConfig, z: float | None) -> str:
    base = (
        f"Period-over-period change at {cfg.grain} grain "
        f"(threshold {cfg.pct_threshold * 100:.0f}%, min magnitude {cfg.min_abs:g})"
    )
    if z is not None:
        return f"{base}; robust median/MAD z-score = {z:.2f}."
    return f"{base}; insufficient history for a z-score."


def _headline(
    metric: Metric,
    current: TrendPoint,
    prior: TrendPoint,
    change_pct: float,
    direction: Direction,
    root_cause: RootCause | None,
) -> str:
    verb = "fell" if direction == "down" else "rose"
    text = (
        f"{metric.label} {verb} {abs(change_pct) * 100:.1f}% in {current.period} "
        f"vs {prior.period}, from {_fmt(prior.value, metric.format)} "
        f"to {_fmt(current.value, metric.format)}."
    )
    if root_cause is not None:
        share = abs(root_cause.contribution_pct)
        text += (
            f" {root_cause.segment} ({root_cause.dimension}) drove most of the move "
            f"({_fmt(root_cause.delta, metric.format)}, {share:.0f}% of the change)."
        )
    return text


def _indian_group(value: float, decimals: int = 0) -> str:
    """Group digits on the Indian scale: 1152000 -> ``11,52,000``.

    The last three digits are grouped together, then every two before that, so
    the standard ``{:,}`` (which groups in threes throughout) cannot be used.
    """
    sign = "-" if value < 0 else ""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join([*pairs, tail])
    return sign + whole + (f".{frac}" if frac else "")


def _quarter_bounds(period: str) -> tuple[str, str] | None:
    """``"2026Q2"`` -> ``("2026-04-01", "2026-06-30")``; ``None`` if not a quarter."""
    if "Q" not in period:
        return None
    year_str, _, q_str = period.partition("Q")
    if not (year_str.isdigit() and q_str.isdigit()):
        return None
    q = int(q_str)
    if not 1 <= q <= 4:
        return None
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    end_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    return f"{year_str}-{start_month:02d}-01", f"{year_str}-{end_month:02d}-{end_day}"
