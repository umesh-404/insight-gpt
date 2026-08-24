"""Deterministic SQL builder.

Takes a *validated selection* (metric + dimensions + filters + grain) and
compiles it to a single parameterized ``SELECT``. The LLM produces the
selection; this code produces the SQL, using only the joins and aggregations
declared in the semantic catalog. That is the reliability keystone from
``docs/05-insight-engine.md`` §3: the model cannot invent a join or an
aggregation because it never writes SQL.

Placeholders are emitted as ``?`` with an ordered params list. The DuckDB
fixture executor consumes this directly; the Postgres executor translates ``?``
to ``%s`` (see ``app/warehouse/executor.py``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .catalog import CatalogError, SemanticCatalog


class Filter(BaseModel):
    dimension: str
    op: str  # "between" (date range) | "in" (categorical/entity)
    values: list[str | int | float]


class MetricSelection(BaseModel):
    """A governed selection — the LLM's structured output for the SQL path."""

    metric: str
    dimensions: list[str] = Field(default_factory=list)
    time_grain: str | None = None
    filters: list[Filter] = Field(default_factory=list)
    order_by_metric: str | None = None  # "asc" | "desc" | None
    limit: int | None = None


class BuiltQuery(BaseModel):
    sql: str
    params: list[str | int | float]
    columns: list[str]  # output column names in order (dims..., metric)


def build_query(selection: MetricSelection, catalog: SemanticCatalog) -> BuiltQuery:
    metric = catalog.resolve_metric(selection.metric)
    fact = catalog.facts[metric.fact]

    # --- validate every referenced dimension against the metric's allow-list ---
    referenced = list(selection.dimensions) + [f.dimension for f in selection.filters]
    for dname in referenced:
        dim = catalog.resolve_dimension(dname)  # raises on unknown dimension
        # A filter on the metric's own date/entity is always allowed; grouping
        # dims must be in the metric's declared dimension set.
        if dname in selection.dimensions and dname not in metric.dimensions:
            raise CatalogError(
                f"metric {metric.name!r} cannot be sliced by {dname!r}; "
                f"allowed: {sorted(metric.dimensions)}"
            )
        if dim.table not in fact.joins:
            raise CatalogError(
                f"dimension {dname!r} is not joinable from fact {fact.name!r}"
            )

    # --- collect the dim tables we actually need to join ----------------------
    needed_tables: list[str] = []
    for dname in referenced:
        table = catalog.dimensions[dname].table
        if table not in needed_tables:
            needed_tables.append(table)

    # --- SELECT + GROUP BY ----------------------------------------------------
    select_parts: list[str] = []
    columns: list[str] = []
    for dname in selection.dimensions:
        dim = catalog.dimensions[dname]
        expr = dim.grain_expr(selection.time_grain)
        select_parts.append(f"{expr} AS {dname}")
        columns.append(dname)
    select_parts.append(f"{metric.expr} AS {metric.name}")
    columns.append(metric.name)

    # --- FROM + JOINs ---------------------------------------------------------
    lines = [f"SELECT {', '.join(select_parts)}", f"FROM {fact.name}"]
    for table in needed_tables:
        join_col = fact.joins[table]
        lines.append(f"JOIN {table} ON {fact.name}.{join_col} = {table}.{join_col}")

    # --- WHERE (parameterized) ------------------------------------------------
    params: list[str | int | float] = []
    where: list[str] = []
    for f in selection.filters:
        dim = catalog.dimensions[f.dimension]
        if f.op == "between":
            if not dim.is_date() or not dim.time_column:
                raise CatalogError(f"'between' only valid on a date dimension, got {f.dimension!r}")
            if len(f.values) != 2:
                raise CatalogError("'between' filter needs exactly two values [start, end]")
            where.append(f"{dim.table}.{dim.time_column} BETWEEN ? AND ?")
            params.extend(f.values)
        elif f.op == "in":
            if not f.values:
                raise CatalogError(f"'in' filter on {f.dimension!r} needs at least one value")
            col = f"{dim.table}.{dim.key}" if dim.key and _looks_like_ids(f.values) else \
                  f"{dim.table}.{dim.expr}"
            placeholders = ", ".join("?" for _ in f.values)
            where.append(f"{col} IN ({placeholders})")
            params.extend(f.values)
        else:
            raise CatalogError(f"unsupported filter op {f.op!r}")
    if where:
        lines.append("WHERE " + " AND ".join(where))

    # --- GROUP BY (positional, portable across Postgres + DuckDB) --------------
    if selection.dimensions:
        positions = ", ".join(str(i + 1) for i in range(len(selection.dimensions)))
        lines.append(f"GROUP BY {positions}")

    # --- ORDER BY -------------------------------------------------------------
    if selection.order_by_metric in ("asc", "desc"):
        lines.append(f"ORDER BY {metric.name} {selection.order_by_metric.upper()}")
    elif selection.dimensions:
        lines.append("ORDER BY 1")

    # --- LIMIT (hard-capped) --------------------------------------------------
    limit = selection.limit or catalog.default_rows
    limit = min(limit, catalog.max_rows)
    lines.append(f"LIMIT {int(limit)}")

    return BuiltQuery(sql="\n".join(lines), params=params, columns=columns)


def _looks_like_ids(values: list[str | int | float]) -> bool:
    """Heuristic: all-int values filter on the surrogate key, else on the label."""
    return all(isinstance(v, int) for v in values)
