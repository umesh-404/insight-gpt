"""Read-only warehouse execution.

``Warehouse`` is the interface the engine depends on. Two implementations:

* ``DuckDBWarehouse`` — an in-process fixture warehouse seeded with a small
  retail star schema, so the whole engine runs with **no external database**.
* ``PostgresWarehouse`` — the production executor (lands in the API phase);
  connects as a read-only role and translates ``?`` placeholders to ``%s``.

Both run every query through the guardrails first (defense in depth), even
though the SQL is machine-generated from a validated selection.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..engine.guardrails import validate_sql


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list]


class Warehouse(Protocol):
    def run(self, sql: str, params: list) -> QueryResult: ...


class DuckDBWarehouse:
    """In-memory fixture warehouse. Deterministic; safe to rebuild each run."""

    def __init__(self, allow_tables: set[str]):
        import duckdb  # local import: only the fixture path needs duckdb

        self._allow_tables = allow_tables
        self._con = duckdb.connect(database=":memory:")
        from ..fixtures.retail import build_retail_warehouse

        build_retail_warehouse(self._con)

    def run(self, sql: str, params: list) -> QueryResult:
        # Guardrails run against the same dialect we execute (belt-and-braces).
        validate_sql(sql, self._allow_tables, dialect="duckdb")
        cur = self._con.execute(sql, params)
        columns = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        return QueryResult(columns=columns, rows=rows)


class PostgresWarehouse:
    """Production executor. Connects as a read-only role over the marts schema."""

    def __init__(self, dsn: str, allow_tables: set[str], statement_timeout_ms: int = 10000,
                 search_path: str = "marts, public"):
        self._dsn = dsn
        self._allow_tables = allow_tables
        self._timeout = statement_timeout_ms
        # dbt builds the star schema into the `marts` schema; the builder emits
        # unqualified table names, so the connection must resolve them there.
        self._search_path = search_path

    def run(self, sql: str, params: list) -> QueryResult:
        import psycopg  # local import: only the production path needs psycopg

        validate_sql(sql, self._allow_tables, dialect="postgres")
        pg_sql = sql.replace("?", "%s")  # our builder emits qmark placeholders
        with psycopg.connect(self._dsn, autocommit=True) as con:
            con.execute(f"SET search_path TO {self._search_path}")
            con.execute(f"SET statement_timeout = {int(self._timeout)}")
            cur = con.execute(pg_sql, params)
            columns = [d.name for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
        return QueryResult(columns=columns, rows=rows)
