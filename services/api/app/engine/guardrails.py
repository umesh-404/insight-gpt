"""SQL guardrails — the hard security boundary around every executed query.

The structured path *generates* SQL from a validated selection, so this is
mostly belt-and-braces; it exists so that a bug in the builder, or any future
free-form escape hatch, still cannot do damage. We parse the SQL with sqlglot
(not regex) and reject anything that is not a single, read-only ``SELECT`` over
allow-listed tables. See ``docs/08-security.md`` and ``docs/05-insight-engine.md``
§4.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


class GuardrailError(Exception):
    """Raised when SQL fails a safety check. Never retried by loosening rules."""


# Statement types that must never appear anywhere in the tree.
_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Merge, exp.Command, exp.Grant,
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

    # Every referenced base table must be in the allow-list.
    referenced = _referenced_tables(root)
    illegal = referenced - allow_tables
    if illegal:
        raise GuardrailError(f"query references non-allow-listed table(s): {sorted(illegal)}")

    # A SELECT with no table at all is suspicious in this engine.
    if not referenced:
        raise GuardrailError("query references no allow-listed table")

    return root


def _referenced_tables(root: exp.Expression) -> set[str]:
    """Collect base-table names, excluding CTE aliases defined in the same query."""
    cte_names = {cte.alias_or_name for cte in root.find_all(exp.CTE)}
    tables: set[str] = set()
    for t in root.find_all(exp.Table):
        name = t.name
        if name and name not in cte_names:
            tables.add(name)
    return tables


def assert_safe(sql: str, allow_tables: set[str], *, dialect: str = "postgres") -> None:
    """Convenience wrapper: validate and discard the AST."""
    validate_sql(sql, allow_tables, dialect=dialect)
