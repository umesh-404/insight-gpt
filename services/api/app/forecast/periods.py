"""Period-label arithmetic for the governed date grains.

The semantic layer labels periods as strings (``2026Q2``, ``2026-05``,
``2026-W20``, ``2026``, or an ISO date for the day grain). Forecasting needs two
things from those labels: the labels of the *next* h periods, and whether the
observed history has holes in it. Both live here so the engine stays about
statistics.

Unknown or unparseable labels never raise — they degrade to synthetic
``"<last>+1"`` style labels and "no gap detected", because a cosmetic label is
not worth failing a forecast over.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

#: Governed grain -> the seasonal cycle length in periods (1 = no seasonality).
SEASON_LENGTH: dict[str, int] = {
    "day": 7,
    "week": 52,
    "month": 12,
    "quarter": 4,
    "year": 1,
}

_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_WEEK = re.compile(r"^(\d{4})-W(\d{1,2})$")
_YEAR = re.compile(r"^(\d{4})$")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def season_length(grain: str) -> int:
    return SEASON_LENGTH.get(grain, 1)


def next_period(label: str, grain: str) -> str | None:
    """The label immediately following ``label`` at ``grain``, or ``None``."""
    text = label.strip()
    if grain == "quarter":
        m = _QUARTER.match(text)
        if m:
            year, q = int(m.group(1)), int(m.group(2))
            return f"{year + 1}Q1" if q == 4 else f"{year}Q{q + 1}"
    if grain == "month":
        m = _MONTH.match(text)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            return f"{year + 1}-01" if month == 12 else f"{year}-{month + 1:02d}"
    if grain == "week":
        m = _WEEK.match(text)
        if m:
            year, week = int(m.group(1)), int(m.group(2))
            return f"{year + 1}-W01" if week >= 52 else f"{year}-W{week + 1:02d}"
    if grain == "year":
        m = _YEAR.match(text)
        if m:
            return str(int(m.group(1)) + 1)
    if grain == "day":
        m = _DAY.match(text)
        if m:
            try:
                day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
            return (day + timedelta(days=1)).isoformat()
    return None


def future_periods(last: str, grain: str, horizon: int) -> list[str]:
    """The next ``horizon`` labels after ``last`` (synthetic if unparseable)."""
    labels: list[str] = []
    cursor = last
    for step in range(1, horizon + 1):
        nxt = next_period(cursor, grain) if cursor is not None else None
        if nxt is None:
            # Unknown label shape: stay honest about ordering without inventing
            # a calendar we cannot parse.
            labels.append(f"{last}+{step}")
            cursor = None  # type: ignore[assignment]
            continue
        labels.append(nxt)
        cursor = nxt
    return labels


def missing_periods(labels: list[str], grain: str) -> list[str]:
    """Labels absent between the first and last observed period.

    A gap means the series is not evenly spaced, which quietly invalidates the
    "one row = one step" assumption every smoothing method makes.
    """
    if len(labels) < 2:
        return []
    gaps: list[str] = []
    observed = set(labels)
    cursor: str | None = labels[0]
    # Bound the walk so a mixed-grain label set cannot spin forever.
    for _ in range(len(labels) * 64):
        if cursor is None or cursor == labels[-1]:
            break
        cursor = next_period(cursor, grain)
        if cursor is None:
            return []  # unparseable labels: report no gaps rather than guess
        if cursor != labels[-1] and cursor not in observed:
            gaps.append(cursor)
    return gaps
