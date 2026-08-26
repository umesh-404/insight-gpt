"""The structured path: governed selections -> SQL -> rows -> findings.

Given the router's output, this module builds governed ``MetricSelection``s,
executes them through the query builder + guardrails + warehouse, and assembles
typed *findings* the synthesis step narrates. For "why did X change?" questions
it applies the total -> by-dimension -> contribution template deterministically.
No free SQL is ever produced here.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..providers.base import Provider
from ..semantic.catalog import SemanticCatalog
from ..semantic.query_builder import Filter, MetricSelection
from ..warehouse.executor import Warehouse
from .contribution import contribution, rows_to_map
from .envelope import CorrectionAttempt, Table
from .selfcorrect import AbstainSignal, Corrector, ExecOutcome, suggest_metrics


class StructuredResult(BaseModel):
    sql: list[str]
    tables: list[Table]
    findings: dict
    # Bounded self-correction record + overall status ("ok" | "no_data").
    attempts: list[CorrectionAttempt] = []
    status: str = "ok"


def _run_sel(corr: Corrector, selection: MetricSelection, stage: str,
             attempts: list[CorrectionAttempt], *, expect_rows: bool = False) -> ExecOutcome:
    """Execute one governed selection through the corrector, collecting attempts.

    A selection that cannot be made valid within the retry budget aborts the
    whole structured answer via :class:`AbstainSignal` — the engine refuses
    rather than emit a half-fabricated result.
    """
    out = corr.execute(selection, stage=stage, expect_rows=expect_rows)
    attempts.extend(out.attempts)
    if out.status == "failed":
        raise AbstainSignal(
            "I could not build a valid governed query for that question, even "
            "after self-correction.",
            suggest_metrics(selection.metric, corr.catalog),
            attempts=list(attempts),
        )
    return out


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


def _format_of(catalog: SemanticCatalog, metric: str) -> str:
    """The governed display format for ``metric`` (``currency``/``percent``/...)."""
    try:
        return catalog.resolve_metric(metric).format
    except Exception:  # noqa: BLE001 - an unknown metric simply has no format
        return "number"


def run_structured(route: dict, catalog: SemanticCatalog, warehouse: Warehouse,
                   provider: Provider, question: str = "") -> StructuredResult:
    metric = route["metric"] or "revenue"
    time_range = route["time_range"]
    sql: list[str] = []
    tables: list[Table] = []
    attempts: list[CorrectionAttempt] = []
    corr = Corrector(catalog, warehouse, provider, question)

    # --- change question: total -> by-dimension -> contribution ---------------
    if route.get("is_change_question") and route.get("prior_time_range"):
        prior_range = route["prior_time_range"]

        cur_total = _scalar(metric, time_range, corr, sql, attempts)
        prior_total = _scalar(metric, prior_range, corr, sql, attempts)
        change_abs = cur_total - prior_total
        change_pct = (change_abs / prior_total * 100.0) if prior_total else 0.0

        # trend table (drives the chart)
        trend = MetricSelection(
            metric=metric, dimensions=["date"], time_grain="quarter",
            filters=[Filter(dimension="date", op="between",
                            values=[prior_range["start"], time_range["end"]])],
        )
        out_trend = _run_sel(corr, trend, "trend:date", attempts)
        sql.append(out_trend.built.sql)
        res = out_trend.result
        tables.append(Table(title=f"{metric} by quarter", columns=res.columns, rows=res.rows))

        by_region = _breakdown(metric, "region", time_range, prior_range, corr, sql, attempts)
        by_category = _breakdown(metric, "category", time_range, prior_range, corr, sql, attempts)
        tables.append(_delta_table(f"{metric} change by region", by_region))
        tables.append(_delta_table(f"{metric} change by category", by_category))

        findings = {
            "kind": "change",
            "metric": metric,
            "format": _format_of(catalog, metric),
            "current": {"label": _period_label(time_range), "value": cur_total},
            "prior": {"label": _period_label(prior_range), "value": prior_total},
            "change_abs": change_abs,
            "change_pct": round(change_pct, 1),
            "by_region": by_region,
            "by_category": by_category,
            "top_region": (by_region[0] if by_region else None),
            "top_category": (by_category[0] if by_category else None),
        }
        return StructuredResult(sql=sql, tables=tables, findings=findings, attempts=attempts)

    # --- grouped question -----------------------------------------------------
    if route.get("group_dims"):
        dim = route["group_dims"][0]
        selection = MetricSelection(
            metric=metric, dimensions=[dim], filters=[_time_filter(time_range)],
            order_by_metric="desc",
        )
        out = _run_sel(corr, selection, f"grouped:{dim}", attempts, expect_rows=True)
        sql.append(out.built.sql)
        eff_metric = out.selection.metric
        eff_dims = out.selection.dimensions
        if out.status == "empty":
            return _no_data(eff_metric, time_range, sql, tables, attempts)
        # Correction may have dropped the grouping dimension; fall back to a
        # scalar reading rather than mis-indexing an ungrouped row.
        if not eff_dims:
            value = float(out.result.rows[0][0]) if out.result.rows else 0.0
            tables.append(Table(title=f"{eff_metric}", columns=[eff_metric], rows=[[value]]))
            findings = {"kind": "scalar", "metric": eff_metric,
                        "format": _format_of(catalog, eff_metric),
                        "period": _period_label(time_range), "value": value}
            return StructuredResult(sql=sql, tables=tables, findings=findings, attempts=attempts)
        eff_dim = eff_dims[0]
        tables.append(Table(title=f"{eff_metric} by {eff_dim}",
                            columns=out.result.columns, rows=out.result.rows))
        rows = [{"label": r[0], "value": float(r[1])} for r in out.result.rows]
        findings = {"kind": "grouped", "metric": eff_metric, "dimension": eff_dim,
                    "format": _format_of(catalog, eff_metric),
                    "period": _period_label(time_range), "rows": rows}
        return StructuredResult(sql=sql, tables=tables, findings=findings, attempts=attempts)

    # --- scalar question ------------------------------------------------------
    selection = MetricSelection(metric=metric, dimensions=[], filters=[_time_filter(time_range)])
    out = _run_sel(corr, selection, f"scalar:{metric}", attempts, expect_rows=True)
    sql.append(out.built.sql)
    eff_metric = out.selection.metric
    if out.status == "empty":
        return _no_data(eff_metric, time_range, sql, tables, attempts)
    value = float(out.result.rows[0][0]) if out.result.rows and out.result.rows[0][0] is not None \
        else 0.0
    tables.append(Table(title=f"{eff_metric}", columns=[eff_metric], rows=[[value]]))
    findings = {"kind": "scalar", "metric": eff_metric,
                "format": _format_of(catalog, eff_metric),
                "period": _period_label(time_range), "value": value}
    return StructuredResult(sql=sql, tables=tables, findings=findings, attempts=attempts)


def _no_data(metric: str, time_range: dict, sql: list[str], tables: list[Table],
             attempts: list[CorrectionAttempt]) -> StructuredResult:
    """A well-formed, governed query that genuinely matched no rows.

    This is *not* an abstention and *not* a fabricated ``0`` — it is an explicit,
    honest empty result the engine narrates plainly (see engine ``_no_data``).
    """
    tables.append(Table(title=f"{metric}", columns=[metric], rows=[]))
    findings = {"kind": "no_data", "metric": metric, "period": _period_label(time_range)}
    return StructuredResult(sql=sql, tables=tables, findings=findings,
                            attempts=attempts, status="no_data")


def _scalar(metric: str, time_range: dict, corr: Corrector, sql: list[str],
            attempts: list[CorrectionAttempt]) -> float:
    selection = MetricSelection(metric=metric, dimensions=[], filters=[_time_filter(time_range)])
    out = _run_sel(corr, selection, f"scalar:{metric}", attempts)
    sql.append(out.built.sql)
    if not out.result.rows or out.result.rows[0][0] is None:
        return 0.0
    return float(out.result.rows[0][0])


def _breakdown(metric: str, dim: str, cur_range: dict, prior_range: dict,
               corr: Corrector, sql: list[str], attempts: list[CorrectionAttempt]) -> list[dict]:
    cur_sel = MetricSelection(
        metric=metric, dimensions=[dim], filters=[_time_filter(cur_range)])
    cur_out = _run_sel(corr, cur_sel, f"breakdown:{dim}:current", attempts)
    sql.append(cur_out.built.sql)
    prior_sel = MetricSelection(
        metric=metric, dimensions=[dim], filters=[_time_filter(prior_range)])
    prior_out = _run_sel(corr, prior_sel, f"breakdown:{dim}:prior", attempts)
    sql.append(prior_out.built.sql)

    deltas = contribution(rows_to_map(cur_out.result.rows), rows_to_map(prior_out.result.rows))
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
