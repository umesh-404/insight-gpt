"""Adversarial suite: the SQL boundary (OWASP 2026 LLM Top 10, blast radius).

The framing this suite is written to: *assume the model is fooled, and prove the
deterministic layers make that boring.* So nothing here asks whether a prompt can
be tricked. It asks what a trickster can actually reach — and answers with the
two mechanisms that decide it: the guardrail parser
(:mod:`app.engine.guardrails`) and the query builder
(:mod:`app.semantic.query_builder`), which is the only thing in the system that
ever authors SQL.

Covers:

* **LLM06 / excessive agency + SQL injection**: a fuzz corpus of stacked
  statements, comment obfuscation, writing CTEs, ``SELECT ... INTO``, ``COPY``,
  file-reading and sleeping functions, catalog probes, ``UNION`` reach-out,
  cross-database qualification, and row locks. Every one must be rejected.
* **Unbounded consumption**: the ``LIMIT`` cap and the statement timeout cannot
  be raised by anything a selection can express.
* **Injection through user-supplied filter values**: an entity literally named
  ``North'; DROP TABLE dim_customer;--`` is a bound parameter, never SQL.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.engine.guardrails import GuardrailError, validate_sql
from app.semantic.catalog import load_catalog
from app.semantic.query_builder import Filter, MetricSelection, build_query
from app.warehouse.executor import DuckDBWarehouse, PostgresWarehouse

ALLOW = {
    "fact_order_items", "fact_inventory_snapshot", "dim_date",
    "dim_product", "dim_customer", "dim_store", "dim_channel",
}
DIALECTS = ("postgres", "duckdb")

# Every payload below is a *rejection* requirement. Grouped by the technique it
# uses, because a failure here should name the technique that got through.
REJECTED_SQL: list[tuple[str, str]] = [
    # --- stacked statements ---------------------------------------------------
    ("stacked", "SELECT 1; DROP TABLE dim_date"),
    ("stacked", "SELECT * FROM fact_order_items; SELECT * FROM dim_date;"),
    ("stacked", "SELECT * FROM dim_date;\nTRUNCATE TABLE fact_order_items"),
    # --- comment obfuscation --------------------------------------------------
    ("comment", "SELECT/*x*/1; DROP TABLE dim_date"),
    ("comment", "SELECT 1 -- harmless\n; DROP TABLE dim_date"),
    ("comment", "SELECT * FROM dim_date /*! ; DROP TABLE dim_date */; DROP TABLE dim_date"),
    # --- writing CTEs ---------------------------------------------------------
    ("cte-write", "WITH x AS (INSERT INTO dim_date VALUES (1) RETURNING *) SELECT * FROM x"),
    ("cte-write", "WITH x AS (UPDATE dim_date SET date_key = 1 RETURNING *) SELECT * FROM x"),
    ("cte-write", "WITH x AS (DELETE FROM dim_date RETURNING *) SELECT * FROM x"),
    # --- SELECT ... INTO: a write wearing a SELECT's clothes ------------------
    ("select-into", "SELECT * INTO evil FROM fact_order_items"),
    # Both names are allow-listed, so only an explicit Into check stops this one.
    ("select-into", "SELECT * INTO dim_date FROM fact_order_items"),
    # --- bulk export ----------------------------------------------------------
    ("copy", "COPY fact_order_items TO '/tmp/exfil.csv'"),
    ("copy", "COPY (SELECT * FROM fact_order_items) TO '/tmp/exfil.csv'"),
    ("copy", "\\copy fact_order_items TO '/tmp/exfil.csv'"),
    # --- filesystem / network reach-out through functions ---------------------
    ("file-read", "SELECT pg_read_file('/etc/passwd') FROM dim_date"),
    ("file-read", "SELECT pg_read_binary_file('/etc/passwd') FROM dim_date"),
    ("file-read", "SELECT lo_import('/etc/passwd') FROM dim_date"),
    ("file-read", "SELECT * FROM fact_order_items JOIN read_csv('/etc/passwd') f ON TRUE"),
    ("file-read", "SELECT * FROM fact_order_items JOIN read_parquet('s3://x/y') f ON TRUE"),
    ("file-read", "SELECT * FROM fact_order_items JOIN read_json('/etc/passwd') f ON TRUE"),
    ("file-read", "SELECT * FROM fact_order_items, read_csv_auto('/etc/passwd')"),
    ("network", "SELECT dblink('dbname=x', 'SELECT 1') FROM dim_date"),
    ("network", "SELECT query_to_xml('SELECT 1', true, true, '') FROM dim_date"),
    # --- denial of service ----------------------------------------------------
    ("dos-sleep", "SELECT pg_sleep(60) FROM dim_date"),
    ("dos-sleep", "SELECT sleep(60) FROM dim_date"),
    ("dos-sleep", "SELECT * FROM fact_order_items WHERE pg_sleep(60) IS NULL"),
    # --- set-returning / table functions --------------------------------------
    ("srf", "SELECT * FROM generate_series(1, 1000000)"),
    ("srf", "SELECT * FROM pg_sleep(10)"),
    # --- catalog and configuration probes -------------------------------------
    ("catalog-probe", "SELECT * FROM information_schema.tables"),
    ("catalog-probe", "SELECT * FROM information_schema.columns"),
    ("catalog-probe", "SELECT usename, passwd FROM pg_shadow"),
    ("catalog-probe", "SELECT * FROM pg_catalog.pg_user"),
    ("catalog-probe", "SELECT current_setting('data_directory') FROM dim_date"),
    ("catalog-probe", "SELECT set_config('statement_timeout', '0', false) FROM dim_date"),
    ("catalog-probe", "SELECT pg_ls_dir('/') FROM dim_date"),
    # --- reaching an unmodeled table -----------------------------------------
    ("reach-out", "SELECT region FROM fact_order_items UNION SELECT usename FROM pg_shadow"),
    ("reach-out", "SELECT (SELECT max(secret) FROM raw_secrets) FROM dim_date"),
    ("reach-out", "SELECT * FROM fact_order_items CROSS JOIN (SELECT * FROM raw_pii) p"),
    ("reach-out", "SELECT * FROM fact_order_items f, dim_date d, pg_user u"),
    ("reach-out", "SELECT * FROM raw.customer_pii"),
    # --- qualification bypass: the bare name is allow-listed, the table is not -
    ("qualified", "SELECT * FROM other_db.public.fact_order_items"),
    ("qualified", "SELECT * FROM pg_temp.dim_date"),
    # --- write intent expressed as a lock -------------------------------------
    ("lock", "SELECT * FROM fact_order_items FOR UPDATE"),
    ("lock", "SELECT * FROM fact_order_items FOR SHARE"),
    # --- side effects through functions ---------------------------------------
    ("side-effect", "SELECT setval('order_seq', 1) FROM dim_date"),
    ("side-effect", "SELECT nextval('order_seq') FROM dim_date"),
    ("side-effect", "SELECT pg_terminate_backend(1) FROM dim_date"),
    # --- DDL / DML outright ---------------------------------------------------
    ("ddl", "DROP TABLE dim_date"),
    ("ddl", "CREATE TEMP TABLE t AS SELECT * FROM fact_order_items"),
    ("ddl", "ALTER TABLE fact_order_items ADD COLUMN x INT"),
    ("ddl", "GRANT ALL ON fact_order_items TO PUBLIC"),
    ("dml", "DELETE FROM fact_order_items"),
    ("dml", "UPDATE fact_order_items SET quantity = 0"),
    ("dml", "INSERT INTO dim_date VALUES (1)"),
    ("dml", "MERGE INTO dim_date d USING dim_date s ON TRUE WHEN MATCHED THEN DELETE"),
    # --- no governed table at all --------------------------------------------
    ("no-table", "SELECT 1"),
    ("no-table", "SELECT version()"),
    ("no-table", "SELECT current_user"),
]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    ("technique", "sql"), REJECTED_SQL, ids=[f"{t}:{s[:44]}" for t, s in REJECTED_SQL]
)
def test_hostile_sql_is_rejected(technique: str, sql: str, dialect: str) -> None:
    """No payload in the corpus may reach the database, in either dialect."""
    with pytest.raises(GuardrailError):
        validate_sql(sql, ALLOW, dialect=dialect)


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", [
    "SELECT SUM(gross_revenue - discount_amount) AS revenue FROM fact_order_items LIMIT 1000",
    "SELECT region, SUM(gross_revenue) AS revenue FROM fact_order_items "
    "JOIN dim_customer ON fact_order_items.customer_key = dim_customer.customer_key "
    "GROUP BY 1 ORDER BY 1 LIMIT 1000",
    "SELECT SUM(CASE WHEN is_returned THEN 0 ELSE quantity END) AS units_sold "
    "FROM fact_order_items LIMIT 1000",
    "SELECT SUM(a) / NULLIF(COUNT(DISTINCT order_key), 0) AS aov FROM fact_order_items LIMIT 10",
    "SELECT COUNT(*) AS n FROM fact_order_items",
])
def test_governed_shapes_still_pass(sql: str, dialect: str) -> None:
    """The hardening must not break the shapes the builder actually emits."""
    validate_sql(sql, ALLOW, dialect=dialect)


def test_every_builder_emitted_query_passes_its_own_guardrail() -> None:
    """Whatever the builder emits is, by construction, guardrail-clean."""
    catalog = load_catalog()
    for metric in catalog.metrics.values():
        for dim in [None, *metric.dimensions]:
            selection = MetricSelection(
                metric=metric.name,
                dimensions=[] if dim is None else [dim],
                time_grain="quarter" if dim == "date" else None,
            )
            built = build_query(selection, catalog)
            validate_sql(built.sql, set(catalog.allow_tables), dialect="duckdb")


# --- unbounded consumption ----------------------------------------------------
def test_limit_cap_cannot_be_raised_by_a_selection() -> None:
    catalog = load_catalog()
    built = build_query(
        MetricSelection(metric="revenue", dimensions=["region"], limit=10**9), catalog
    )
    assert built.sql.rstrip().endswith(f"LIMIT {catalog.max_rows}")
    assert "10000000" not in built.sql


def test_every_built_query_carries_a_bounded_limit() -> None:
    catalog = load_catalog()
    for metric in catalog.metrics:
        built = build_query(MetricSelection(metric=metric, limit=10**6), catalog)
        limit = int(built.sql.rsplit("LIMIT ", 1)[1].strip())
        assert 0 < limit <= catalog.max_rows


class _FakeCursor:
    description = ()

    def fetchall(self):
        return []


class _FakeConnection:
    def __init__(self, log: list[tuple[str, list]]):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._log.append((sql, list(params or [])))
        return _FakeCursor()


@pytest.fixture()
def psycopg_log(monkeypatch: pytest.MonkeyPatch):
    """Stub ``psycopg`` so the production executor runs offline and is observable."""
    log: list[tuple[str, list]] = []
    module = types.ModuleType("psycopg")
    module.connect = lambda dsn, autocommit=False: _FakeConnection(log)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)
    return log


def test_statement_timeout_and_search_path_are_set_on_every_connection(psycopg_log) -> None:
    catalog = load_catalog()
    warehouse = PostgresWarehouse(
        dsn="postgresql://ro@localhost/insightgpt",
        allow_tables=set(catalog.allow_tables),
        statement_timeout_ms=catalog.statement_timeout_ms,
    )
    built = build_query(MetricSelection(metric="revenue", limit=10**9), catalog)
    warehouse.run(built.sql, built.params)

    statements = [sql for sql, _ in psycopg_log]
    assert any(s.startswith("SET search_path TO") for s in statements)
    assert f"SET statement_timeout = {catalog.statement_timeout_ms}" in statements
    # The session settings are issued before the query, so a long-running query
    # is already bounded when it starts.
    assert statements.index(f"SET statement_timeout = {catalog.statement_timeout_ms}") < len(
        statements
    ) - 1
    assert statements[-1].rstrip().endswith(f"LIMIT {catalog.max_rows}")


def test_a_crafted_selection_cannot_reach_the_timeout_setting(psycopg_log) -> None:
    """The timeout comes from the catalog; no selection field can influence it."""
    catalog = load_catalog()
    warehouse = PostgresWarehouse(
        dsn="postgresql://ro@localhost/insightgpt",
        allow_tables=set(catalog.allow_tables),
        statement_timeout_ms=catalog.statement_timeout_ms,
    )
    hostile = MetricSelection(
        metric="revenue",
        dimensions=["region"],
        filters=[Filter(dimension="region", op="in",
                        values=["North'; SET statement_timeout = 0; --"])],
    )
    built = build_query(hostile, catalog)
    warehouse.run(built.sql, built.params)

    settings = [sql for sql, _ in psycopg_log if sql.startswith("SET statement_timeout")]
    assert settings == [f"SET statement_timeout = {catalog.statement_timeout_ms}"]
    # The hostile literal travelled as a bound parameter, not as SQL text.
    assert "SET statement_timeout = 0" not in built.sql
    assert any("North'; SET statement_timeout = 0; --" in params
               for _, params in psycopg_log if params)


# --- injection through user-supplied filter values -----------------------------
_INJECTION_LITERALS = [
    "North'; DROP TABLE dim_customer;--",
    "North' OR '1'='1",
    "North'/**/UNION/**/SELECT/**/1--",
    "North\\'; DELETE FROM fact_order_items; --",
    "'; COPY fact_order_items TO '/tmp/x'; --",
    'North" OR 1=1 --',
]


@pytest.mark.parametrize("literal", _INJECTION_LITERALS)
def test_entity_filter_values_are_bound_parameters_not_sql(literal: str) -> None:
    catalog = load_catalog()
    built = build_query(
        MetricSelection(
            metric="revenue",
            dimensions=["region"],
            filters=[Filter(dimension="region", op="in", values=[literal])],
        ),
        catalog,
    )
    # The literal is nowhere in the SQL text; the SQL carries a placeholder.
    assert literal not in built.sql
    assert "DROP" not in built.sql.upper()
    assert "dim_customer.region IN (?)" in built.sql
    assert built.params == [literal]
    validate_sql(built.sql, set(catalog.allow_tables), dialect="duckdb")


@pytest.mark.parametrize("literal", _INJECTION_LITERALS)
def test_injection_literal_executes_as_a_harmless_no_match(literal: str) -> None:
    """End to end: the hostile 'region name' just matches nothing."""
    catalog = load_catalog()
    warehouse = DuckDBWarehouse(allow_tables=set(catalog.allow_tables))
    built = build_query(
        MetricSelection(
            metric="revenue",
            dimensions=["region"],
            filters=[Filter(dimension="region", op="in", values=[literal])],
        ),
        catalog,
    )
    assert warehouse.run(built.sql, built.params).rows == []
    # The table the payload tried to drop is still there and still populated.
    intact = warehouse.run("SELECT COUNT(*) AS n FROM dim_customer", [])
    assert intact.rows[0][0] > 0


@pytest.mark.parametrize("value", [
    "2026-01-01' OR 1=1 --",
    "2026-01-01'); DROP TABLE dim_date; --",
    "'; SELECT pg_sleep(60); --",
])
def test_date_range_values_are_bound_parameters_not_sql(value: str) -> None:
    catalog = load_catalog()
    built = build_query(
        MetricSelection(
            metric="revenue",
            filters=[Filter(dimension="date", op="between", values=[value, "2026-06-30"])],
        ),
        catalog,
    )
    assert value not in built.sql
    assert "dim_date.full_date BETWEEN ? AND ?" in built.sql
    assert built.params == [value, "2026-06-30"]
    validate_sql(built.sql, set(catalog.allow_tables), dialect="duckdb")


def test_warehouse_refuses_attacker_sql_even_if_handed_it_directly() -> None:
    """The last line: a caller that bypassed the builder entirely still fails."""
    catalog = load_catalog()
    warehouse = DuckDBWarehouse(allow_tables=set(catalog.allow_tables))
    for sql in ("DROP TABLE dim_customer",
                "DELETE FROM fact_order_items",
                "SELECT * INTO dim_date FROM fact_order_items",
                "SELECT * FROM other_db.public.fact_order_items"):
        with pytest.raises(GuardrailError):
            warehouse.run(sql, [])
    assert warehouse.run("SELECT COUNT(*) AS n FROM dim_customer", []).rows[0][0] > 0
