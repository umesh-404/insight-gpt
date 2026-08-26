"""The pure-Python forecasting fallback: damped Holt trend + additive season.

No numpy, no pandas, no compiler — stdlib only. This is what runs in CI, in the
offline default, and in the container image unless the optional ``forecast``
extra is installed. It is deliberately a *small, explainable* model:

1. **Detrend and estimate seasonality.** If the grain has a cycle (4 for
   quarters, 12 for months, ...) and the history covers at least two full
   cycles, fit a least-squares line, average the residuals by position in the
   cycle, and centre those averages to mean zero. That is a seasonal-naive
   profile estimated on detrended data — cheap and hard to get badly wrong.
   Fewer than two cycles: no seasonal term at all, and the caller says so.
2. **Fit a damped Holt linear trend** to the deseasonalised series in
   error-correction form, choosing ``(alpha, beta, phi)`` by a small grid search
   that minimises one-step-ahead squared error. Damping (``phi < 1``) keeps the
   trend from extrapolating a straight line into fantasy.
3. **Project** ``level + sum(phi**i * trend)`` for each step, adding the
   seasonal term back.
4. **Interval.** ``sigma`` is the residual standard error of the one-step-ahead
   fit; the h-step interval is ``z * sigma * sqrt(h)``, widening with horizon the
   way a random-walk error accumulates. ``z`` comes from the normal quantile for
   the requested level. On a constant history ``sigma`` is exactly 0 and the
   interval collapses to zero width — narrow, but honest, and never a division
   by zero.

The interval is a *fit-residual* interval, not a full parameter-uncertainty
interval: it understates risk on very short histories. That is precisely why the
engine refuses to forecast at all below its minimum-history floor.
"""

from __future__ import annotations

from statistics import NormalDist

#: (alpha, beta, phi) search grid. Small enough to be instant, wide enough to
#: cover flat, trending, and noisy series.
_ALPHAS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
_BETAS = (0.0, 0.05, 0.1, 0.2, 0.3, 0.5)
_PHIS = (0.85, 0.92, 0.98)


class Fit:
    """The fitted state of the fallback model."""

    __slots__ = ("alpha", "beta", "level", "phi", "sigma", "trend")

    def __init__(
        self, level: float, trend: float, sigma: float, alpha: float, beta: float, phi: float
    ) -> None:
        self.level = level
        self.trend = trend
        self.sigma = sigma
        self.alpha = alpha
        self.beta = beta
        self.phi = phi


def z_score(level: float) -> float:
    """Two-sided normal quantile for a prediction level (0.8 -> 1.2816)."""
    level = min(max(level, 0.5), 0.999)
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def linear_fit(values: list[float]) -> tuple[float, float]:
    """Least-squares ``(slope, intercept)`` of ``values`` against 0..n-1."""
    n = len(values)
    if n < 2:
        return 0.0, (values[0] if values else 0.0)
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    var_x = sum((i - mean_x) ** 2 for i in range(n))
    if var_x == 0:
        return 0.0, mean_y
    cov = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    slope = cov / var_x
    return slope, mean_y - slope * mean_x


def seasonal_indices(values: list[float], m: int) -> list[float] | None:
    """Additive, mean-zero seasonal profile of length ``m``, or ``None``.

    Returns ``None`` unless the history spans at least two full cycles — with
    one cycle the "seasonal" term is indistinguishable from the noise.
    """
    if m <= 1 or len(values) < 2 * m:
        return None
    slope, intercept = linear_fit(values)
    residuals = [v - (intercept + slope * i) for i, v in enumerate(values)]
    profile: list[float] = []
    for position in range(m):
        bucket = residuals[position::m]
        profile.append(sum(bucket) / len(bucket) if bucket else 0.0)
    centre = sum(profile) / m
    return [p - centre for p in profile]


def _run(values: list[float], alpha: float, beta: float, phi: float) -> tuple[float, float, float]:
    """One pass of damped Holt; returns ``(level, trend, sse)``."""
    level = values[0]
    trend = values[1] - values[0]
    sse = 0.0
    for y in values[1:]:
        pred = level + phi * trend
        err = y - pred
        sse += err * err
        level = pred + alpha * err
        trend = phi * trend + beta * err
    return level, trend, sse


def fit_holt(values: list[float]) -> Fit:
    """Grid-search a damped Holt trend minimising one-step-ahead squared error."""
    if len(values) < 2:
        return Fit(values[0] if values else 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

    best: tuple[float, float, float, float, float] | None = None  # sse,l,b,a,beta
    best_phi = _PHIS[-1]
    for phi in _PHIS:
        for alpha in _ALPHAS:
            for beta in _BETAS:
                level, trend, sse = _run(values, alpha, beta, phi)
                if best is None or sse < best[0]:
                    best = (sse, level, trend, alpha, beta)
                    best_phi = phi
    assert best is not None
    sse, level, trend, alpha, beta = best
    # Residual standard error. Two parameters (level, trend) are consumed by the
    # fit, so the denominator is (#errors - 2), floored at 1.
    dof = max(1, (len(values) - 1) - 2)
    sigma = (sse / dof) ** 0.5
    return Fit(level, trend, sigma, alpha, beta, best_phi)


def project(
    values: list[float], horizon: int, m: int, interval_level: float
) -> tuple[list[tuple[float, float, float]], Fit, bool]:
    """Forecast ``horizon`` steps.

    Returns ``([(point, lower, upper), ...], fit, seasonal_used)``.
    """
    indices = seasonal_indices(values, m)
    if indices is None:
        deseasonalised = list(values)
    else:
        deseasonalised = [v - indices[i % m] for i, v in enumerate(values)]

    fit = fit_holt(deseasonalised)
    z = z_score(interval_level)
    n = len(values)

    out: list[tuple[float, float, float]] = []
    cumulative_trend = 0.0
    for step in range(1, horizon + 1):
        cumulative_trend += fit.phi**step * fit.trend
        point = fit.level + cumulative_trend
        if indices is not None:
            point += indices[(n + step - 1) % m]
        half = z * fit.sigma * (step**0.5)
        out.append((point, point - half, point + half))
    return out, fit, indices is not None
