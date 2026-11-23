"""Optional ``statsforecast`` backend, detected at runtime.

``statsforecast`` gives us AutoETS/AutoTheta — properly selected exponential
smoothing with model-based prediction intervals — but it drags in numpy, pandas
and numba. That is a heavy, compiler-adjacent dependency chain for a project
whose default posture is "runs offline on a modest laptop", so it lives in the
optional ``forecast`` extra and is imported **lazily**:

* installed  -> used when the history is long enough to justify model selection;
* absent     -> the pure-Python fallback in :mod:`.smoothing` runs instead.

Either way the result names the method that produced it — down to the ETS form
AutoETS selected, because "we asked for a seasonal period" is not the same claim
as "the fitted model has a seasonal term" — so nobody has to guess which engine,
or which model, a number came from. Every failure inside the optional path is
swallowed and reported as unavailable — an optional accelerator must never be
able to fail a request.
"""

from __future__ import annotations

import importlib.util
import warnings
from functools import lru_cache
from typing import NamedTuple


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


class BackendForecast(NamedTuple):
    """What the optional backend produced, including what it actually fitted.

    ``seasonal`` is the selected model's *own* seasonal term: ``True``/``False``
    when the fitted form could be read, ``None`` when it could not. The engine
    is not allowed to advertise a seasonal term it cannot confirm, so "unknown"
    is a distinct answer rather than a default of ``True``.
    """

    points: list[tuple[float, float, float]]
    method: str
    seasonal: bool | None


def statsforecast_project(
    values: list[float], horizon: int, season_length: int, interval_level: float
) -> BackendForecast | None:
    """Forecast with AutoETS; ``None`` if the optional backend cannot deliver."""
    try:
        import numpy as np
        from statsforecast.models import AutoETS

        level_pct = int(round(interval_level * 100))
        model = AutoETS(season_length=max(1, season_length))
        # ``fit`` + ``predict`` rather than the one-shot ``forecast``: only the
        # fitted model records which ETS form AutoETS selected, and we report
        # that instead of assuming the season length we asked for was used.
        #
        # The RuntimeWarning suppressed here is ``ets.py`` dividing by a zero
        # residual variance on a perfect in-sample fit. It is not ignored — it
        # surfaces either as a non-finite bound (rejected just below, so the
        # fallback runs) or as a zero-width interval, which the engine widens
        # and explains in a caveat. Leaving the raw warning on an operator's
        # console instead of handling the condition is what we are fixing.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            model.fit(y=np.asarray(values, dtype="float64"))
            out = model.predict(h=horizon, level=[level_pct])
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
        form, seasonal = _selected_form(model)
        name = "statsforecast AutoETS" + (f" ({form})" if form else "")
        return BackendForecast(points=points, method=name, seasonal=seasonal)
    except Exception:  # noqa: BLE001 — an optional accelerator never fails a request
        return None


def _selected_form(model: object) -> tuple[str | None, bool | None]:
    """The fitted ETS form (e.g. ``ETS(A,A,A)``) and whether it is seasonal.

    ``(None, None)`` when the backend does not expose what it selected — in
    which case the caller must say "unknown", not "seasonal".
    """
    fitted = getattr(model, "model_", None)
    if not isinstance(fitted, dict):
        return None, None
    form = fitted.get("method")
    form = str(form) if isinstance(form, str) and form else None
    # ``components`` is (error, trend, season, damped), e.g. "AAAN"; the third
    # letter is "N" when the selected model carries no seasonal term.
    components = fitted.get("components")
    seasonal: bool | None = None
    if isinstance(components, str) and len(components) >= 3:
        seasonal = components[2].upper() != "N"
    return form, seasonal


def _is_bad(value: float) -> bool:
    return value != value or value in (float("inf"), float("-inf"))
