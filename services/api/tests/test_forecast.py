"""Forecasting (docs/13-forecasting.md) — fully offline, fallback-only.

These tests pin the behaviour that actually matters: the engine *refuses* on the
two-quarter demo warehouse instead of emitting a confident line, every emitted
point is bracketed by its own interval, ratio metrics are forecast directly
rather than summed, a constant series does not explode, and every failure path
lands on a clean 4xx/5xx envelope rather than a 500.

Nothing here needs the optional ``statsforecast`` extra: the pure-Python
fallback is the path under test, and the optional backend is exercised through
its own availability probe and a stubbed module.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_warehouse, reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.forecast import backends  # noqa: E402
from app.forecast.engine import (  # noqa: E402
    ForecastConfig,
    ForecastError,
    build_result,
    fetch_history,
    forecast_metric,
    forecastability,
)
from app.forecast.models import HistoryPoint  # noqa: E402
from app.forecast.periods import future_periods, missing_periods  # noqa: E402
from app.semantic.catalog import CatalogError, load_catalog  # noqa: E402
from app.warehouse.executor import DuckDBWarehouse, QueryResult  # noqa: E402

QUARTERS = [f"{year}Q{q}" for year in (2022, 2023, 2024, 2025) for q in (1, 2, 3, 4)]


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def fixture_warehouse(catalog) -> DuckDBWarehouse:
    """The real two-quarter demo warehouse."""
    return DuckDBWarehouse(set(catalog.allow_tables))


class SeriesWarehouse:
    """A warehouse stub that replays a synthetic (period, value) series."""

    def __init__(self, values: list[float], periods: list[str] | None = None):
        self.periods = periods or QUARTERS[: len(values)]
        self.values = values
        self.calls: list[tuple[str, list]] = []

    def run(self, sql: str, params: list) -> QueryResult:
        self.calls.append((sql, list(params)))
        rows = [[p, v] for p, v in zip(self.periods, self.values, strict=True)]
        return QueryResult(columns=["date", "value"], rows=rows)


class DeadWarehouse:
    def run(self, sql: str, params: list) -> QueryResult:
        raise ConnectionError("connection refused: could not connect to warehouse")


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    return TestClient(create_app())


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> dict[str, str]:
    return _login(client, "analyst@insightgpt.dev", "analyst-pass")


@pytest.fixture(scope="module")
def viewer(client: TestClient) -> dict[str, str]:
    return _login(client, "viewer@insightgpt.dev", "viewer-pass")


# --------------------------------------------------------------------------
# the happy path: enough history -> a bracketed forecast with a named method
# --------------------------------------------------------------------------


def test_enough_history_produces_bracketed_forecast_with_a_method(catalog) -> None:
    values = [100.0 + 5.0 * i + (3.0 if i % 2 else -3.0) for i in range(16)]
    warehouse = SeriesWarehouse(values)

    result = forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=3)

    assert result.n_history == 16
    assert len(result.forecast) == 3
    assert result.method_family in ("fallback", "statsforecast")
    assert result.method and "none" not in result.method
    assert result.confidence in ("medium", "high")
    assert result.low_confidence is False

    for point in result.forecast:
        assert point.lower <= point.value <= point.upper
        assert point.upper > point.lower  # a noisy series must carry real width
    # The interval widens with the horizon: uncertainty accumulates.
    widths = [p.upper - p.lower for p in result.forecast]
    assert widths[0] < widths[-1]
    # The trend is upward, so the projection should sit above the last observation.
    assert result.forecast[0].value > values[-1] - 20
    # Future labels continue the calendar rather than repeating the last period.
    assert result.forecast[0].period == "2026Q1"
    assert result.headline.startswith("Revenue is projected at")


def test_governed_path_is_used_not_free_sql(catalog) -> None:
    warehouse = SeriesWarehouse([100.0] * 8)
    forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=1)
    sql, _params = warehouse.calls[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert "GROUP BY" in sql and "LIMIT" in sql
    assert "quarter_label" in sql  # the grain came from the catalog, not a string


def test_filters_flow_through_the_query_builder(catalog) -> None:
    warehouse = SeriesWarehouse([100.0] * 8)
    from app.semantic.query_builder import Filter

    forecast_metric(
        "revenue",
        catalog,
        warehouse,
        grain="quarter",
        horizon=1,
        filters=[Filter(dimension="region", op="in", values=["North"])],
    )
    sql, params = warehouse.calls[0]
    assert "dim_customer" in sql and "IN (?)" in sql
    assert params == ["North"]


# --------------------------------------------------------------------------
# the honest refusal: the real demo warehouse holds two quarters
# --------------------------------------------------------------------------


def test_demo_warehouse_has_too_little_history_and_says_so(
    catalog, fixture_warehouse
) -> None:
    history = fetch_history("revenue", catalog, fixture_warehouse, "quarter")
    assert len(history) == 2, "the fixture warehouse is expected to hold two quarters"

    result = forecast_metric("revenue", catalog, fixture_warehouse, grain="quarter")

    assert result.forecast == []          # no numbers at all
    assert result.confidence == "none"
    assert result.low_confidence is True
    assert result.method_family == "none"
    assert result.n_history == 2
    assert "Not enough history" in result.headline
    assert any("Refused to forecast" in c for c in result.caveats)
    # And the history it *does* have is still returned, for a chart.
    assert [p.period for p in result.history] == ["2026Q1", "2026Q2"]


def test_refusal_over_the_api_is_200_with_no_numbers(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/forecast",
        json={"metric": "revenue", "grain": "quarter", "horizon": 2},
        headers=analyst,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["forecast"] == []
    assert body["confidence"] == "none"
    assert body["low_confidence"] is True
    assert body["n_history"] == 2


def test_forecastability_report_explains_every_metric(
    client: TestClient, viewer: dict[str, str]
) -> None:
    resp = client.get("/api/v1/forecast/metrics?grain=quarter", headers=viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grain"] == "quarter"
    assert body["min_history"] >= 2
    keys = {m["metric"] for m in body["metrics"]}
    assert {"revenue", "gross_margin_pct", "units_on_hand"} <= keys
    for row in body["metrics"]:
        assert row["forecastable"] is False       # two quarters of demo data
        assert "Not enough history" in row["reason"]
        assert row["n_history"] < body["min_history"]


# --------------------------------------------------------------------------
# ratio metrics are forecast directly, never summed
# --------------------------------------------------------------------------


def test_ratio_metric_is_forecast_directly_and_flagged(catalog) -> None:
    # A margin percentage hovering around 32%.
    values = [0.30, 0.31, 0.33, 0.32, 0.34, 0.33, 0.35, 0.34, 0.33, 0.36]
    warehouse = SeriesWarehouse(values)

    result = forecast_metric("gross_margin_pct", catalog, warehouse, grain="quarter")

    assert result.additive is False
    assert any("ratio metric" in c for c in result.caveats)
    point = result.forecast[0]
    # Forecast in the metric's own units — nowhere near a sum of the periods.
    assert 0.0 <= point.value <= 1.0
    assert point.value < sum(values)
    assert 0.0 <= point.lower <= point.upper <= 1.0  # clamped to a valid ratio
    # A single dimensionless slice never appears in the SQL for the history read.
    sql, _ = warehouse.calls[0]
    assert "SUM(gross_margin_pct" not in sql


def test_ratio_metric_bounds_are_clamped_to_the_unit_interval(catalog) -> None:
    values = [0.02, 0.01, 0.03, 0.02, 0.01, 0.02, 0.03, 0.01]
    warehouse = SeriesWarehouse(values)
    result = forecast_metric("return_rate", catalog, warehouse, grain="quarter")
    for point in result.forecast:
        assert point.lower >= 0.0  # a negative return rate is not a thing
        assert point.upper <= 1.0


# --------------------------------------------------------------------------
# degenerate inputs
# --------------------------------------------------------------------------


def test_constant_series_does_not_explode(catalog) -> None:
    warehouse = SeriesWarehouse([250.0] * 12)
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=4)

    for point in result.forecast:
        assert point.value == pytest.approx(250.0, abs=1e-6)
        assert point.lower == pytest.approx(point.value, abs=1e-6)
        assert point.upper == pytest.approx(point.value, abs=1e-6)
        assert point.lower <= point.value <= point.upper
    assert any("perfectly constant" in c for c in result.caveats)


def test_zero_series_is_finite(catalog) -> None:
    warehouse = SeriesWarehouse([0.0] * 8)
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=2)
    for point in result.forecast:
        assert point.value == pytest.approx(0.0, abs=1e-9)
        assert point.value == point.value  # not NaN


def test_empty_history_refuses_without_raising(catalog) -> None:
    warehouse = SeriesWarehouse([])
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter")
    assert result.forecast == []
    assert result.n_history == 0
    assert any("no rows" in c for c in result.caveats)


def test_single_point_history_refuses(catalog) -> None:
    warehouse = SeriesWarehouse([42.0])
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter")
    assert result.forecast == []
    assert result.confidence == "none"


def test_missing_periods_are_reported_as_a_caveat(catalog) -> None:
    periods = ["2024Q1", "2024Q2", "2024Q4", "2025Q1", "2025Q2", "2025Q3"]
    warehouse = SeriesWarehouse([10.0, 12.0, 15.0, 14.0, 16.0, 18.0], periods=periods)
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter")
    assert any("missing" in c for c in result.caveats)


def test_short_history_stays_low_confidence(catalog) -> None:
    warehouse = SeriesWarehouse([10.0, 12.0, 11.0, 13.0])
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=1)
    assert result.forecast  # above the floor, so it will forecast...
    assert result.confidence == "low"  # ...but never confidently
    assert result.low_confidence is True
    assert any("Low confidence" in c for c in result.caveats)


def test_long_horizon_downgrades_confidence(catalog) -> None:
    warehouse = SeriesWarehouse([100.0 + i for i in range(16)])
    near = forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=2)
    far = forecast_metric("revenue", catalog, warehouse, grain="quarter", horizon=12)
    assert near.confidence == "high"
    assert far.confidence == "low"
    assert any("Horizon" in c for c in far.caveats)


def test_monthly_grain_labels_and_seasonality(catalog) -> None:
    periods = [f"2024-{m:02d}" for m in range(1, 13)] + [
        f"2025-{m:02d}" for m in range(1, 13)
    ]
    values = [100.0 + 2.0 * i + (20.0 if (i % 12) in (10, 11) else 0.0) for i in range(24)]
    warehouse = SeriesWarehouse(values, periods=periods)
    result = forecast_metric("revenue", catalog, warehouse, grain="month", horizon=2)
    assert result.forecast[0].period == "2026-01"
    assert result.forecast[1].period == "2026-02"
    assert "season" in result.method or any("seasonal" in c for c in result.caveats)


# --------------------------------------------------------------------------
# governance + error envelopes (never a 500)
# --------------------------------------------------------------------------


def test_ungoverned_metric_raises_catalog_error(catalog) -> None:
    warehouse = SeriesWarehouse([1.0] * 8)
    with pytest.raises(CatalogError):
        forecast_metric("profit_margin_by_vibes", catalog, warehouse)


def test_ungoverned_metric_over_the_api_is_400(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/forecast",
        json={"metric": "profit_margin_by_vibes", "horizon": 1},
        headers=analyst,
    )
    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["code"] == "bad_request"
    assert "unknown metric" in error["message"]
    assert "revenue" in error["message"]  # the message lists what IS governed


def test_unknown_grain_is_400(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/forecast",
        json={"metric": "revenue", "grain": "fortnight"},
        headers=analyst,
    )
    assert resp.status_code == 400, resp.text
    assert "unknown grain" in resp.json()["error"]["message"]


def test_bad_horizon_is_rejected_not_crashed(catalog) -> None:
    warehouse = SeriesWarehouse([1.0] * 8)
    with pytest.raises(ForecastError):
        forecast_metric("revenue", catalog, warehouse, horizon=99)


def test_out_of_range_horizon_over_the_api_is_422(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/forecast", json={"metric": "revenue", "horizon": 99}, headers=analyst
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_unreachable_warehouse_is_503_not_500(
    client: TestClient, analyst: dict[str, str]
) -> None:
    client.app.dependency_overrides[get_warehouse] = DeadWarehouse
    try:
        resp = client.post("/api/v1/forecast", json={"metric": "revenue"}, headers=analyst)
        listing = client.get("/api/v1/forecast/metrics", headers=analyst)
    finally:
        client.app.dependency_overrides.pop(get_warehouse, None)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "dependency_unavailable"
    assert listing.status_code == 503


def test_warehouse_rejection_is_400_not_500(
    client: TestClient, analyst: dict[str, str]
) -> None:
    class Picky:
        def run(self, sql: str, params: list) -> QueryResult:
            raise ValueError("column does not exist")

    client.app.dependency_overrides[get_warehouse] = Picky
    try:
        resp = client.post("/api/v1/forecast", json={"metric": "revenue"}, headers=analyst)
    finally:
        client.app.dependency_overrides.pop(get_warehouse, None)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


def test_garbage_row_types_do_not_500(client: TestClient, analyst: dict[str, str]) -> None:
    class Garbage:
        def run(self, sql: str, params: list) -> QueryResult:
            return QueryResult(columns=["date", "v"], rows=[["2026Q1", "not-a-number"]])

    client.app.dependency_overrides[get_warehouse] = Garbage
    try:
        resp = client.post("/api/v1/forecast", json={"metric": "revenue"}, headers=analyst)
    finally:
        client.app.dependency_overrides.pop(get_warehouse, None)
    assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


def test_viewer_may_not_create_a_forecast(
    client: TestClient, viewer: dict[str, str]
) -> None:
    resp = client.post("/api/v1/forecast", json={"metric": "revenue"}, headers=viewer)
    assert resp.status_code == 403
    body = resp.json()["error"]
    assert body["code"] == "forbidden"
    assert body["details"]["required"] == "analyst"


def test_anonymous_is_401(client: TestClient) -> None:
    assert client.post("/api/v1/forecast", json={"metric": "revenue"}).status_code == 401
    assert client.get("/api/v1/forecast/metrics").status_code == 401


# --------------------------------------------------------------------------
# the optional backend
# --------------------------------------------------------------------------


def test_offline_default_reports_the_pure_python_fallback(catalog) -> None:
    backends.reset_backend_cache()
    warehouse = SeriesWarehouse([100.0 + i for i in range(12)])
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter")
    if not backends.statsforecast_available():
        assert result.method_family == "fallback"
        assert "pure-Python" in result.method


def test_optional_backend_failure_falls_back(monkeypatch, catalog) -> None:
    """A broken optional accelerator degrades; it never fails the request."""
    monkeypatch.setattr("app.forecast.engine.statsforecast_available", lambda: True)
    monkeypatch.setattr(
        "app.forecast.engine.statsforecast_project",
        lambda *a, **k: None,  # simulates an import/fit failure inside the extra
    )
    warehouse = SeriesWarehouse([100.0 + i for i in range(12)])
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter")
    assert result.method_family == "fallback"
    assert result.forecast


def test_optional_backend_result_is_used_when_it_works(monkeypatch, catalog) -> None:
    monkeypatch.setattr("app.forecast.engine.statsforecast_available", lambda: True)
    monkeypatch.setattr(
        "app.forecast.engine.statsforecast_project",
        lambda values, h, m, level: ([(500.0, 400.0, 600.0)] * h, "statsforecast AutoETS"),
    )
    warehouse = SeriesWarehouse([100.0 + i for i in range(12)])
    result = forecast_metric("revenue", catalog, warehouse, grain="quarter")
    assert result.method_family == "statsforecast"
    assert result.method == "statsforecast AutoETS"
    assert result.forecast[0].value == pytest.approx(500.0)
    assert result.forecast[0].lower == pytest.approx(400.0)


# --------------------------------------------------------------------------
# period arithmetic
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("last", "grain", "expected"),
    [
        ("2026Q4", "quarter", ["2027Q1", "2027Q2"]),
        ("2026-12", "month", ["2027-01", "2027-02"]),
        ("2026", "year", ["2027", "2028"]),
        ("2026-W51", "week", ["2026-W52", "2027-W01"]),
        ("2026-06-30", "day", ["2026-07-01", "2026-07-02"]),
    ],
)
def test_future_period_labels(last: str, grain: str, expected: list[str]) -> None:
    assert future_periods(last, grain, 2) == expected


def test_unparseable_labels_degrade_instead_of_raising() -> None:
    assert future_periods("period-x", "quarter", 2) == ["period-x+1", "period-x+2"]
    assert missing_periods(["period-x", "period-y"], "quarter") == []


def test_missing_periods_detection() -> None:
    assert missing_periods(["2026Q1", "2026Q3"], "quarter") == ["2026Q2"]
    assert missing_periods(["2026Q1", "2026Q2", "2026Q3"], "quarter") == []


# --------------------------------------------------------------------------
# capability report on a synthetic long history
# --------------------------------------------------------------------------


def test_forecastability_flips_to_true_with_enough_history(catalog) -> None:
    warehouse = SeriesWarehouse([100.0 + i for i in range(12)])
    report = forecastability(catalog, warehouse, grain="quarter")
    assert all(row.forecastable for row in report.metrics)
    ratio = next(r for r in report.metrics if r.metric == "gross_margin_pct")
    assert "never summed" in ratio.reason


def test_build_result_is_pure_and_reusable(catalog) -> None:
    metric = catalog.resolve_metric("revenue")
    history = [
        HistoryPoint(period=f"2025Q{i + 1}", value=float(100 + i)) for i in range(4)
    ]
    result = build_result(metric, history, "quarter", 1, ForecastConfig())
    assert result.n_history == 4
    assert result.forecast[0].period == "2026Q1"
