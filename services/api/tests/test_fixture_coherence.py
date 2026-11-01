"""The demo warehouse has to survive an examiner dividing one column by another.

The generator used to derive revenue from a daily target and *then* back out a
quantity (``qty = net_rev // base_price``), which pinned every costly SKU at one
unit: a ₹2,49,999 laptop showed 144 units for ₹6.39L, an implied ₹4,437 a unit,
and the average order value came out below the cheapest product in the
catalogue. Revenue now follows from the price list — ``gross = qty x unit
price`` — and the daily target drives the quantity instead.
"""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")

from app.fixtures.retail import PRODUCTS, build_retail_warehouse  # noqa: E402

PRICES = {p[0]: p[4] for p in PRODUCTS}
CHEAPEST = min(PRICES.values())
DEAREST = max(PRICES.values())

NET = "SUM(CASE WHEN is_returned THEN 0 ELSE gross_revenue - discount_amount END)"
UNITS = "SUM(CASE WHEN is_returned THEN 0 ELSE quantity END)"
Q2 = (
    "FROM fact_order_items f JOIN dim_date d ON f.date_key = d.date_key "
    "WHERE d.quarter_label = '2026Q2'"
)


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(":memory:")
    build_retail_warehouse(c)
    return c


def test_gross_revenue_is_exactly_quantity_times_a_listed_price(con) -> None:
    rows = con.execute(
        "SELECT product_key, quantity, gross_revenue FROM fact_order_items"
    ).fetchall()
    assert rows
    bad = [r for r in rows if r[2] != r[1] * PRICES[r[0]]]
    assert not bad, f"{len(bad)} lines whose gross revenue is not qty x list price: {bad[:3]}"


def test_implied_unit_price_sits_inside_the_price_list(con) -> None:
    implied = con.execute(f"SELECT {NET} * 1.0 / {UNITS} {Q2}").fetchone()[0]
    assert CHEAPEST * 0.5 <= implied <= DEAREST, (
        f"blended unit price {implied:,.0f} is outside the catalogue "
        f"({CHEAPEST:,}-{DEAREST:,})"
    )


def test_average_order_value_is_not_below_the_cheapest_product(con) -> None:
    aov = con.execute(f"SELECT {NET} * 1.0 / COUNT(DISTINCT order_key) {Q2}").fetchone()[0]
    assert aov > CHEAPEST, (
        f"average order value {aov:,.0f} is below the cheapest SKU {CHEAPEST:,} — "
        "an order cannot be worth less than the least a customer can buy"
    )


def test_every_product_in_the_catalogue_actually_sells(con) -> None:
    sold = con.execute("SELECT COUNT(DISTINCT product_key) FROM fact_order_items").fetchone()[0]
    assert sold == len(PRODUCTS)


def test_the_planted_q2_dip_survives(con) -> None:
    """The demo narrative — the North Electronics Q2 2026 collapse — must hold."""
    by_quarter = dict(
        con.execute(
            f"SELECT d.quarter_label, {NET} "
            "FROM fact_order_items f "
            "JOIN dim_date d ON f.date_key = d.date_key "
            "JOIN dim_customer c ON f.customer_key = c.customer_key "
            "JOIN dim_product p ON f.product_key = p.product_key "
            "WHERE c.region = 'North' AND p.category = 'Electronics' "
            "AND d.quarter_label IN ('2026Q1', '2026Q2') GROUP BY 1"
        ).fetchall()
    )
    q1, q2 = by_quarter["2026Q1"], by_quarter["2026Q2"]
    assert q2 < q1 * 0.6, f"the planted dip is gone: Q1 {q1:,} -> Q2 {q2:,}"


def test_revenue_excludes_returns_but_the_gross_figure_is_still_recoverable(con) -> None:
    net, with_returns = con.execute(
        f"SELECT {NET}, SUM(gross_revenue - discount_amount) {Q2}"
    ).fetchone()
    assert net < with_returns, "some lines must be returned for the test to mean anything"
    # Returned lines are ~6% of lines, so the overstatement the old convention
    # carried was of that order.
    assert 0.02 < (with_returns - net) / with_returns < 0.12


def test_the_generator_is_deterministic() -> None:
    def totals() -> tuple:
        c = duckdb.connect(":memory:")
        build_retail_warehouse(c)
        return c.execute(
            "SELECT COUNT(*), SUM(quantity), SUM(gross_revenue), SUM(discount_amount), "
            "SUM(cost_amount) FROM fact_order_items"
        ).fetchone()

    assert totals() == totals()
