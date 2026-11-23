import pytest

from app.semantic.catalog import CatalogError, load_catalog
from app.semantic.query_builder import Filter, MetricSelection, build_query

CAT = load_catalog()


def test_revenue_by_region_builds_join_and_group():
    sel = MetricSelection(
        metric="revenue", dimensions=["region"],
        filters=[Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])],
    )
    built = build_query(sel, CAT)
    assert "FROM fact_order_items" in built.sql
    assert "JOIN dim_customer" in built.sql       # region lives on dim_customer
    assert "JOIN dim_date" in built.sql            # from the date filter
    assert "GROUP BY 1" in built.sql
    assert built.columns == ["region", "revenue"]
    assert built.params == ["2026-04-01", "2026-06-30"]


def test_alias_resolves():
    sel = MetricSelection(metric="aov", dimensions=[])   # alias of avg_order_value
    built = build_query(sel, CAT)
    assert "avg_order_value" in built.sql


def test_unknown_metric_rejected():
    with pytest.raises(CatalogError):
        build_query(MetricSelection(metric="profit_margin_xyz"), CAT)


def test_illegal_dimension_for_metric_rejected():
    # return_rate is not allowed to be sliced by region in the catalog
    with pytest.raises(CatalogError):
        build_query(MetricSelection(metric="return_rate", dimensions=["region"]), CAT)


def test_limit_is_capped():
    sel = MetricSelection(metric="revenue", limit=10_000_000)
    built = build_query(sel, CAT)
    assert f"LIMIT {CAT.max_rows}" in built.sql


# --- semi-additive snapshot metrics -----------------------------------------
# fact_inventory_snapshot is a periodic snapshot: stock sums across stores and
# SKUs but NOT across dates. Summing a quarter of daily snapshots counts the
# same pallet ~91 times. The DuckDB fixture hides this because it holds exactly
# one snapshot date; the Postgres warehouse snapshots daily, so the catalog
# marks the metric semi-additive and the builder pins the range to its latest
# snapshot instead of summing over it.


def test_units_on_hand_is_declared_semi_additive():
    metric = CAT.resolve_metric("units_on_hand")
    assert metric.semi_additive is True
    # Semi-additive is NOT the same as "ratio": `additive: false` is what the
    # metrics API and the forecaster read as "ratio metric", and stock is not
    # one. The two flags must stay distinct.
    assert metric.additive is True
    assert all(
        not m.semi_additive
        for name, m in CAT.metrics.items()
        if name != "units_on_hand"
    )


def test_semi_additive_metric_pins_a_date_range_to_the_latest_snapshot():
    sel = MetricSelection(
        metric="units_on_hand",
        filters=[Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])],
    )
    built = build_query(sel, CAT)
    assert "BETWEEN ? AND ?" in built.sql          # still bounded by the range
    assert "MAX(" in built.sql                     # ...but pinned inside it
    assert "dim_date.full_date = (" in built.sql
    assert built.params == ["2026-04-01", "2026-06-30"]


def test_additive_metric_keeps_a_plain_range():
    sel = MetricSelection(
        metric="revenue",
        filters=[Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])],
    )
    built = build_query(sel, CAT)
    assert "MAX(" not in built.sql
    assert "dim_date.full_date BETWEEN ? AND ?" in built.sql


def test_semi_additive_day_grain_breakdown_is_left_alone():
    # One output row per snapshot date is already correct — nothing is summed
    # across dates — so the range must survive untouched.
    sel = MetricSelection(
        metric="units_on_hand", dimensions=["date"], time_grain="day",
        filters=[Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])],
    )
    built = build_query(sel, CAT)
    assert "MAX(" not in built.sql


def test_semi_additive_month_grain_is_still_pinned():
    # A month of daily snapshots summed into one row is the same error.
    sel = MetricSelection(
        metric="units_on_hand", dimensions=["date"], time_grain="month",
        filters=[Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])],
    )
    built = build_query(sel, CAT)
    assert "MAX(" in built.sql


# --- returns convention ------------------------------------------------------


def test_order_line_money_metrics_all_exclude_returned_lines():
    """Revenue once counted returned lines while units_sold did not.

    The result was a dashboard reporting revenue that included ~7% of lines it
    was simultaneously reporting as returned. Every money measure over the
    order-line fact must now share units_sold's convention.
    """
    for name in ("revenue", "gross_margin", "gross_margin_pct", "avg_order_value"):
        expr = CAT.resolve_metric(name).expr
        assert "is_returned" in expr, f"{name} does not exclude returned lines: {expr}"


def test_return_rate_still_measures_against_all_lines():
    # The denominator is every line, returned included — that is what makes it
    # a rate rather than a count.
    expr = CAT.resolve_metric("return_rate").expr
    assert "NULLIF(SUM(quantity), 0)" in expr


def test_two_snapshot_dates_do_not_double_the_stock():
    """The pin's whole point, proved against a warehouse with two snapshots.

    The shipped DuckDB fixture holds exactly one snapshot date, which is why
    summing across dates was invisible. Postgres snapshots daily, so this test
    builds the multi-date case the fixture cannot show.
    """
    duckdb = pytest.importorskip("duckdb")
    from app.fixtures.retail import build_retail_warehouse

    con = duckdb.connect(":memory:")
    build_retail_warehouse(con)
    (single_date_total,) = con.execute("SELECT SUM(units_on_hand) FROM fact_inventory_snapshot")\
        .fetchone()

    # Add a second snapshot inside the same quarter, one unit lower per row.
    con.execute("""
        INSERT INTO fact_inventory_snapshot
        SELECT product_key, store_key, 20260601, units_on_hand - 1,
               units_reserved, reorder_point, lead_time_days
        FROM fact_inventory_snapshot
    """)
    (naive_sum,) = con.execute("SELECT SUM(units_on_hand) FROM fact_inventory_snapshot").fetchone()
    assert naive_sum > single_date_total  # the error this guards against

    sel = MetricSelection(
        metric="units_on_hand",
        filters=[Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])],
    )
    built = build_query(sel, CAT)
    (pinned,) = con.execute(built.sql, built.params).fetchone()
    # The latest snapshot in the range is 2026-06-01: one unit less per row.
    rows = con.execute("SELECT COUNT(*) FROM fact_inventory_snapshot WHERE date_key = 20260515")\
        .fetchone()[0]
    assert pinned == single_date_total - rows
