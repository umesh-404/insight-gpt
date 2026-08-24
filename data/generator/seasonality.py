"""Deterministic demand shaping: weekly rhythm + holiday-season curve.

Kept separate so the seasonality is inspectable and testable on its own. All
functions are pure and depend only on the date, so output stays deterministic.
"""

from __future__ import annotations

import datetime as dt
import math

# Multiplicative weekday factors (Mon=0 .. Sun=6). Weekends run hotter.
_WEEKDAY_FACTOR = [0.95, 0.95, 1.0, 1.02, 1.1, 1.2, 1.15]


def weekly_factor(when: dt.date) -> float:
    return _WEEKDAY_FACTOR[when.weekday()]


def holiday_factor(when: dt.date) -> float:
    """A smooth demand bump across Nov–Dec, peaking just before year end."""
    if when.month == 11:
        # Ramp from 1.1 at the start of November to ~1.35 by month end.
        return 1.1 + 0.25 * (when.day / 30.0)
    if when.month == 12:
        # Peak mid/late December, then ease off over the final days.
        peak = 1.6
        distance = abs(when.day - 20) / 20.0
        return max(1.15, peak - 0.45 * distance)
    if when.month == 1 and when.day <= 7:
        # Short post-holiday lull.
        return 0.9
    return 1.0


def annual_wave(when: dt.date) -> float:
    """A gentle yearly sine so non-holiday months are not flat."""
    day_of_year = when.timetuple().tm_yday
    return 1.0 + 0.05 * math.sin(2 * math.pi * day_of_year / 365.0)


def seasonality(when: dt.date) -> float:
    """Combined multiplicative seasonality for a given day."""
    return weekly_factor(when) * holiday_factor(when) * annual_wave(when)
