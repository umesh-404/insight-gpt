"""Deterministic contribution analysis.

Given a metric's values for a current and a prior period, broken down by a
dimension, compute each segment's contribution to the total change and rank
them. This is the reusable "why did *metric* change?" template from
``docs/05-insight-engine.md`` §3.3 — pure arithmetic, no LLM, so the *where* of a
change is never guessed.
"""

from __future__ import annotations

from pydantic import BaseModel


class SegmentDelta(BaseModel):
    label: str
    current: float
    prior: float
    delta: float
    contribution_pct: float  # share of the total change, signed


def contribution(current: dict[str, float], prior: dict[str, float]) -> list[SegmentDelta]:
    """Rank segments by their delta (most negative first)."""
    labels = set(current) | set(prior)
    total_change = sum(current.get(x, 0.0) for x in labels) - sum(prior.get(x, 0.0) for x in labels)
    denom = abs(total_change) or 1.0

    rows = []
    for label in labels:
        cur = float(current.get(label, 0.0))
        pri = float(prior.get(label, 0.0))
        delta = cur - pri
        rows.append(SegmentDelta(
            label=label, current=cur, prior=pri, delta=delta,
            contribution_pct=round(delta / denom * 100.0, 1),
        ))
    rows.sort(key=lambda r: r.delta)  # most negative (biggest decline) first
    return rows


def rows_to_map(rows: list[list], label_idx: int = 0, value_idx: int = 1) -> dict[str, float]:
    """Turn warehouse ``[[label, value], ...]`` rows into a {label: value} map."""
    return {str(r[label_idx]): float(r[value_idx]) for r in rows}
