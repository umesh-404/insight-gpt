"""Optional ``statsforecast`` backend, detected at runtime.

``statsforecast`` gives us AutoETS/AutoTheta — properly selected exponential
smoothing with model-based prediction intervals — but it drags in numpy, pandas
and numba. That is a heavy, compiler-adjacent dependency chain for a project
whose default posture is "runs offline on a modest laptop", so it lives in the
optional ``forecast`` extra and is imported **lazily**:

* installed  -> used when the history is long enough to justify model selection;
* absent     -> the pure-Python fallback in :mod:`.smoothing` runs instead.

Either way the result names the method that produced it, so nobody has to guess
which engine a number came from. Every failure inside the optional path is
swallowed and reported as unavailable — an optional accelerator must never be
able to fail a request.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache


@lru_cache(maxsize=1)
def statsforecast_available() -> bool:
    """True when the optional ``forecast`` extra is importable."""
    for module in ("statsforecast", "numpy"):
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except (ImportError, ValueError):  # broken/partial install
            return False
    return True


def reset_backend_cache() -> None:
    """Drop the availability probe (used by tests that patch the environment)."""
    statsforecast_available.cache_clear()


def statsforecast_project(
    values: list[float], horizon: int, season_length: int, interval_level: float
) -> tuple[list[tuple[float, float, float]], str] | None:
    """Forecast with AutoETS; ``None`` if the optional backend cannot deliver.

    Returns ``([(point, lower, upper), ...], method_name)``.
    """
    try:
        import numpy as np
        from statsforecast.models import AutoETS

        level_pct = int(round(interval_level * 100))
        model = AutoETS(season_length=max(1, season_length))
        out = model.forecast(
            y=np.asarray(values, dtype="float64"), h=horizon, level=[level_pct]
        )
        mean = [float(v) for v in out["mean"]]
        lower = [float(v) for v in out[f"lo-{level_pct}"]]
        upper = [float(v) for v in out[f"hi-{level_pct}"]]
        if not (len(mean) == len(lower) == len(upper) == horizon):
            return None
        points = [
            (m, min(low, m), max(high, m))
            for m, low, high in zip(mean, lower, upper, strict=True)
        ]
        if any(_is_bad(v) for point in points for v in point):
            return None
        return points, "statsforecast AutoETS"
    except Exception:  # noqa: BLE001 — an optional accelerator never fails a request
        return None


def _is_bad(value: float) -> bool:
    return value != value or value in (float("inf"), float("-inf"))
