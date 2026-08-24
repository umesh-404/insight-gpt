"""The structured path: governed selections -> SQL -> rows -> findings.

Given the router's output, this module builds governed ``MetricSelection``s,
executes them through the query builder + guardrails + warehouse, and assembles
typed *findings* the synthesis step narrates. For "why did X change?" questions
it applies the total -> by-dimension -> contribution template deterministically.
No free SQL is ever produced here.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..semantic.catalog import SemanticCatalog
from ..semantic.query_builder import BuiltQuery, Filter, MetricSelection, build_query
from ..warehouse.executor import QueryResult, Warehouse
from .contribution import contribution, rows_to_map
from .envelope import Table


class StructuredResult(BaseModel):
    sql: list[str]
    tables: list[Table]
    findings: dict


def _execute(selection: MetricSelection, catalog: SemanticCatalog,
             warehouse: Warehouse) -> tuple[BuiltQuery, QueryResult]:
    built = build_query(selection, catalog)
    result = warehouse.run(built.sql, built.params)
    return built, result


def _time_filter(time_range: dict) -> Filter:
    return Filter(dimension="date", op="between",
                  values=[time_range["start"], time_range["end"]])


def _period_label(time_range: dict) -> str:
    start, end = time_range["start"], time_range["end"]
    # Recognize a clean calendar quarter for a tidy label.
    q_starts = {"01-01": "Q1", "04-01": "Q2", "07-01": "Q3", "10-01": "Q4"}
    key = start[5:]
    if key in q_starts:
        return f"{start[:4]}{q_starts[key]}"
    return f"{start} to {end}"


def run_structured(route: dict, catalog: SemanticCatalog, warehouse: Warehouse) -> StructuredResult:
    metric = route["metric"] or "revenue"
    time_range = route["time_range"]
    sql: list[str] = []
    tables: list[Table] = []

    # --- change question: total -> by-dimension -> contribution ---------------
    if route.get("is_change_question") and route.get("prior_time_range"):
        prior_range = route["prior_time_range"]

        cur_total = _scalar(metric, time_range, catalog, warehouse, sql)
        prior_total = _scalar(metric, prior_range, catalog, warehouse, sql)
        change_abs = cur_total - prior_total
        change_pct = (change_abs / prior_total * 100.0) if prior_total else 0.0

        # trend table (drives the chart)
        trend = MetricSelection(
            metric=metric, dimensions=["date"], time_grain="quarter",
            filters=[Filter(dimension="date", op="between",
                            values=[prior_range["start"], time_range["end"]])],
        )
        built, res = _execute(trend, catalog, warehouse)
        sql.append(built.sql)
        tables.append(Table(title=f"{metric} by quarter", columns=res.columns, rows=res.rows))

        by_region = _breakdown(
            metric, "region", time_range, prior_range, catalog, warehouse, sql)
        by_category = _breakdown(
            metric, "category", time_range, prior_range, catalog, warehouse, sql)
        tables.append(_delta_table(f"{metric} change by region", by_region))
        tables.append(_delta_table(f"{metric} change by category", by_category))

        findings = {
            "kind": "change",
            "metric": metric,
            "current": {"label": _period_label(time_range), "value": cur_total},
            "prior": {"label": _period_label(prior_range), "value": prior_total},
            "change_abs": change_abs,
            "change_pct": round(change_pct, 1),
            "by_region": by_region,
            "by_category": by_category,
            "top_region": (by_region[0] if by_region else None),
            "top_category": (by_category[0] if by_category else None),
        }
        return StructuredResult(sql=sql, tables=tables, findings=findings)

    # --- grouped question -----------------------------------------------------
    if route.get("group_dims"):
        dim = route["group_dims"][0]
        selection = MetricSelection(
            metric=metric, dimensions=[dim], filters=[_time_filter(time_range)],
            order_by_metric="desc",
        )
        built, res = _execute(selection, catalog, warehouse)
        sql.append(built.sql)
        tables.append(Table(title=f"{metric} by {dim}", columns=res.columns, rows=res.rows))
        rows = [{"label": r[0], "value": float(r[1])} for r in res.rows]
        findings = {"kind": "grouped", "metric": metric, "dimension": dim,
                    "period": _period_label(time_range), "rows": rows}
        return StructuredResult(sql=sql, tables=tables, findings=findings)

    # --- scalar question ------------------------------------------------------
    value = _scalar(metric, time_range, catalog, warehouse, sql)
    tables.append(Table(title=f"{metric}", columns=[metric], rows=[[value]]))
    findings = {"kind": "scalar", "metric": metric,
                "period": _period_label(time_range), "value": value}
    return StructuredResult(sql=sql, tables=tables, findings=findings)


def _scalar(metric: str, time_range: dict, catalog: SemanticCatalog,
            warehouse: Warehouse, sql: list[str]) -> float:
    selection = MetricSelection(metric=metric, dimensions=[], filters=[_time_filter(time_range)])
    built, res = _execute(selection, catalog, warehouse)
    sql.append(built.sql)
    if not res.rows or res.rows[0][0] is None:
        return 0.0
    return float(res.rows[0][0])


def _breakdown(metric: str, dim: str, cur_range: dict, prior_range: dict,
               catalog: SemanticCatalog, warehouse: Warehouse, sql: list[str]) -> list[dict]:
    cur_sel = MetricSelection(
        metric=metric, dimensions=[dim], filters=[_time_filter(cur_range)])
    cur_built, cur_res = _execute(cur_sel, catalog, warehouse)
    sql.append(cur_built.sql)
    prior_sel = MetricSelection(
        metric=metric, dimensions=[dim], filters=[_time_filter(prior_range)])
    prior_built, prior_res = _execute(prior_sel, catalog, warehouse)
    sql.append(prior_built.sql)

    deltas = contribution(rows_to_map(cur_res.rows), rows_to_map(prior_res.rows))
    return [{dim: d.label, "current": d.current, "prior": d.prior,
             "delta": d.delta, "contribution_pct": d.contribution_pct} for d in deltas]


def _delta_table(title: str, rows: list[dict]) -> Table:
    if not rows:
        return Table(title=title, columns=["segment", "prior", "current", "delta"], rows=[])
    dim_key = next(k for k in rows[0] if k not in ("current", "prior", "delta", "contribution_pct"))
    return Table(
        title=title, columns=[dim_key, "prior", "current", "delta"],
        rows=[[r[dim_key], r["prior"], r["current"], r["delta"]] for r in rows],
    )
