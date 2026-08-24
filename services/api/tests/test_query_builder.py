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
