"""SQL guardrails — the hard security boundary around every executed query.

The structured path *generates* SQL from a validated selection, so this is
mostly belt-and-braces; it exists so that a bug in the builder, or any future
free-form escape hatch, still cannot do damage. We parse the SQL with sqlglot
(not regex) and reject anything that is not a single, read-only ``SELECT`` over
allow-listed tables. See ``docs/08-security.md`` and ``docs/05-insight-engine.md``
§4.
"""

from __future__ import annotations

import contextlib

import sqlglot
from sqlglot import exp


class GuardrailError(Exception):
    """Raised when SQL fails a safety check. Never retried by loosening rules."""


# Statement types that must never appear anywhere in the tree.
_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Merge, exp.Command, exp.Grant, exp.Copy,
    # ``SELECT ... INTO t`` is a *write* wearing a SELECT's clothes: sqlglot
    # models it as a modifier on the Select, so the statement-type check above
    # does not see it. Without this the payload
    # ``SELECT * INTO dim_date FROM fact_order_items`` passes every other check
    # because both table names are allow-listed.
    exp.Into,
)

# Server-side functions that reach outside the modeled data: the filesystem,
# another host, the server's configuration, a sequence's state, or the clock.
# None of them can be produced by the query builder (the catalog's only
# aggregations are SUM/COUNT/NULLIF/CASE), so denying them costs nothing and
# closes file-read, SSRF-ish, DoS-by-sleep and write-by-side-effect payloads
# that are otherwise a legal ``SELECT`` over allow-listed tables.
_DENIED_FUNCTIONS = frozenset({
    "sleep", "setval", "nextval", "currval", "current_setting", "set_config",
    "xpath", "xmltable", "glob", "parquet_scan", "system", "shell_exec",
    "version", "current_database", "current_user", "session_user", "user",
    "inet_client_addr", "inet_server_addr", "txid_current",
})
# Whole families are denied by prefix rather than enumerated, so a function we
# have never heard of does not become a bypass.
_DENIED_FUNCTION_PREFIXES = (
    "pg_",        # pg_read_file, pg_sleep, pg_ls_dir, pg_terminate_backend, ...
    "lo_",        # large-object import/export = filesystem access
    "dblink",     # outbound connections from inside the database
    "read_",      # duckdb read_csv/read_parquet/read_json/read_text/read_blob
    "duckdb_",    # duckdb introspection functions
    "query_to_xml",
    "postgres_fdw",
)


def validate_sql(sql: str, allow_tables: set[str], *, dialect: str = "postgres") -> exp.Expression:
    """Parse and validate ``sql``. Returns the AST on success, raises otherwise."""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception as e:  # noqa: BLE001 - surface any parse failure as a guardrail reject
        raise GuardrailError(f"SQL failed to parse: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise GuardrailError(f"expected exactly one statement, got {len(statements)}")

    root = statements[0]

    # Must be a SELECT at the top (a bare SELECT or a SELECT wrapped in WITH).
    if not isinstance(root, (exp.Select, exp.Subquery, exp.With)):
        raise GuardrailError(f"only SELECT is allowed, got {type(root).__name__}")

    # No write/DDL node may appear anywhere in the tree.
    for node in root.walk():
        if isinstance(node, _FORBIDDEN):
            raise GuardrailError(f"forbidden statement type: {type(node).__name__}")
        # Row locks (``FOR UPDATE`` / ``FOR SHARE``) express write intent and
        # take locks on a table we only ever read.
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise GuardrailError("row-locking clauses (FOR UPDATE/SHARE) are not allowed")

    _reject_denied_functions(root)

    # Every referenced base table must be in the allow-list.
    referenced = _referenced_tables(root)
    illegal = referenced - allow_tables
    if illegal:
        raise GuardrailError(f"query references non-allow-listed table(s): {sorted(illegal)}")

    # A SELECT with no table at all is suspicious in this engine.
    if not referenced:
        raise GuardrailError("query references no allow-listed table")

    return root


def _reject_denied_functions(root: exp.Expression) -> None:
    """Reject any call to a function outside the analytic set."""
    for node in root.find_all(exp.Func):
        for name in _function_names(node):
            if name in _DENIED_FUNCTIONS or name.startswith(_DENIED_FUNCTION_PREFIXES):
                raise GuardrailError(f"forbidden function call: {name}")


def _function_names(node: exp.Func) -> set[str]:
    """Every SQL spelling of one function node, lower-cased.

    An unrecognized function parses to :class:`exp.Anonymous` (its name is the
    payload), while a dialect function sqlglot models explicitly carries its
    spellings on the class — ``read_parquet`` becomes ``ReadParquet``, whose
    class name alone would slip past a name-based deny-list.
    """
    if isinstance(node, exp.Anonymous):
        return {str(node.this).lower()}
    names = {type(node).__name__.lower()}
    with contextlib.suppress(Exception):  # a node without declared spellings is fine
        names.update(n.lower() for n in node.sql_names())
    return names


def _referenced_tables(root: exp.Expression) -> set[str]:
    """Collect base-table names, excluding CTE aliases defined in the same query.

    A qualified reference (``schema.t``, ``catalog.schema.t``) is rejected
    outright rather than matched on its bare name: the allow-list holds table
    names, so ``other_db.public.fact_order_items`` would otherwise compare equal
    to the allow-listed ``fact_order_items`` and reach a table we never modeled.
    The query builder and the row-count probe both emit unqualified names — the
    executor pins ``search_path`` — so nothing legitimate is qualified.
    """
    cte_names = {cte.alias_or_name for cte in root.find_all(exp.CTE)}
    tables: set[str] = set()
    for t in root.find_all(exp.Table):
        name = t.name
        if not name or name in cte_names:
            continue
        if t.db or t.catalog:
            raise GuardrailError(
                f"schema/catalog-qualified table reference is not allowed: {t.sql()}"
            )
        tables.add(name)
    return tables


def assert_safe(sql: str, allow_tables: set[str], *, dialect: str = "postgres") -> None:
    """Convenience wrapper: validate and discard the AST."""
    validate_sql(sql, allow_tables, dialect=dialect)
