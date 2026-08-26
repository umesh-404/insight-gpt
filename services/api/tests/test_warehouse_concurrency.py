"""The fixture warehouse must be safe to query from several threads at once.

The API runs warehouse queries in a threadpool, and one screen (the dashboard)
fans out a dozen governed metric queries in parallel. A DuckDB *connection*
holds a single result set and ``execute()`` replaces it, so sharing one
connection across threads made concurrent callers read **each other's rows** —
silently, with a 200 and a well-formed body. That is worse than an error: a
dashboard tile showed another metric's number.

These tests fail against the shared-connection implementation and pass against
the per-call ``cursor()`` one.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("LLM_PROVIDER", "fake")

from app.semantic.catalog import load_catalog  # noqa: E402
from app.semantic.query_builder import Filter, MetricSelection, build_query  # noqa: E402
from app.warehouse.executor import DuckDBWarehouse  # noqa: E402

CATALOG = load_catalog()
Q2 = Filter(dimension="date", op="between", values=["2026-04-01", "2026-06-30"])


def _warehouse() -> DuckDBWarehouse:
    return DuckDBWarehouse(allow_tables=set(CATALOG.allow_tables))


def _built(metric: str, dimensions: list[str] | None = None):
    return build_query(
        MetricSelection(metric=metric, dimensions=dimensions or [], filters=[Q2]),
        CATALOG,
    )


def test_concurrent_queries_each_get_their_own_result() -> None:
    """Every caller must receive the columns and rows of the query IT ran."""
    warehouse = _warehouse()
    # Distinct metrics so a swapped result is detectable by column name alone.
    metrics = ["revenue", "orders", "units_sold", "gross_margin", "return_rate"]
    expected = {m: _built(m) for m in metrics}
    # Sequential baseline: what each query returns when nothing competes.
    baseline = {m: warehouse.run(b.sql, b.params) for m, b in expected.items()}

    def run(metric: str) -> tuple[str, list[str], list[list]]:
        built = expected[metric]
        result = warehouse.run(built.sql, built.params)
        return metric, result.columns, result.rows

    # Repeat: a race does not fail on every attempt.
    for _ in range(12):
        with ThreadPoolExecutor(max_workers=len(metrics)) as pool:
            outcomes = list(pool.map(run, metrics))

        for metric, columns, rows in outcomes:
            assert columns == [metric], (
                f"{metric!r} received another query's columns: {columns}"
            )
            assert rows == baseline[metric].rows, (
                f"{metric!r} received another query's rows"
            )


def test_concurrent_grouped_queries_keep_their_row_counts() -> None:
    """Row counts must not collapse to another query's shape under load."""
    warehouse = _warehouse()
    plans = {
        "by_region": _built("revenue", ["region"]),
        "by_category": _built("revenue", ["category"]),
        "by_product": _built("revenue", ["product"]),
        "scalar": _built("revenue"),
    }
    expected_counts = {
        name: len(warehouse.run(b.sql, b.params).rows) for name, b in plans.items()
    }
    assert expected_counts["scalar"] == 1
    assert expected_counts["by_region"] > 1, "fixture should have several regions"

    def run(name: str) -> tuple[str, int]:
        built = plans[name]
        return name, len(warehouse.run(built.sql, built.params).rows)

    for _ in range(12):
        with ThreadPoolExecutor(max_workers=len(plans)) as pool:
            for name, count in pool.map(run, plans):
                assert count == expected_counts[name], (
                    f"{name!r} returned {count} rows, expected {expected_counts[name]}"
                )


def test_many_parallel_callers_of_one_query_agree() -> None:
    """The same query run 24× in parallel must return one consistent answer."""
    warehouse = _warehouse()
    built = _built("revenue")
    expected = warehouse.run(built.sql, built.params)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(lambda _: warehouse.run(built.sql, built.params), range(24))
        )

    for result in results:
        assert result.columns == expected.columns
        assert result.rows == expected.rows
