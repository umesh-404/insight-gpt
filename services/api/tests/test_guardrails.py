import pytest

from app.engine.guardrails import GuardrailError, validate_sql

ALLOW = {"fact_order_items", "dim_date", "dim_customer"}


def test_valid_select_passes():
    sql = "SELECT region, SUM(gross_revenue) FROM fact_order_items JOIN dim_customer " \
          "ON fact_order_items.customer_key = dim_customer.customer_key GROUP BY 1 LIMIT 100"
    validate_sql(sql, ALLOW, dialect="duckdb")  # should not raise


@pytest.mark.parametrize("sql", [
    "DELETE FROM fact_order_items",
    "UPDATE fact_order_items SET quantity = 0",
    "DROP TABLE dim_date",
    "SELECT 1; DROP TABLE dim_date",              # multi-statement
    "INSERT INTO dim_date VALUES (1)",
])
def test_write_and_ddl_rejected(sql):
    with pytest.raises(GuardrailError):
        validate_sql(sql, ALLOW, dialect="duckdb")


def test_non_allowlisted_table_rejected():
    sql = "SELECT * FROM raw_secrets LIMIT 10"
    with pytest.raises(GuardrailError):
        validate_sql(sql, ALLOW, dialect="duckdb")


def test_reaching_unmodeled_table_in_join_rejected():
    sql = "SELECT * FROM fact_order_items JOIN pg_catalog.pg_user ON true LIMIT 10"
    with pytest.raises(GuardrailError):
        validate_sql(sql, ALLOW, dialect="duckdb")


def test_cte_alias_does_not_whitelist_a_table_in_another_scope():
    """A CTE must only shadow its own name *inside its own scope*.

    CTE names used to be collected from the whole tree, so a subquery could
    declare ``WITH pg_authid AS (...)`` and the outer ``FROM pg_authid`` was
    then skipped as a "CTE reference" — reaching a real, non-allow-listed
    catalog table (the same trick reaches ``raw.*`` staging tables).
    """
    sql = (
        "SELECT * FROM pg_authid WHERE 1 IN ("
        "WITH pg_authid AS (SELECT 1 AS c FROM dim_date) SELECT c FROM pg_authid)"
    )
    for dialect in ("postgres", "duckdb"):
        with pytest.raises(GuardrailError):
            validate_sql(sql, ALLOW, dialect=dialect)


def test_legitimate_cte_still_passes():
    """The scope fix must not reject an ordinary CTE over an allow-listed table."""
    sql = (
        "WITH totals AS (SELECT region, SUM(gross_revenue) AS r FROM fact_order_items "
        "GROUP BY 1) SELECT region, r FROM totals ORDER BY r DESC LIMIT 5"
    )
    for dialect in ("postgres", "duckdb"):
        validate_sql(sql, ALLOW, dialect=dialect)  # should not raise


@pytest.mark.parametrize("sql", [
    "SELECT version() FROM dim_date",       # parses to CurrentVersion, not "version"
    "SELECT user FROM dim_date",            # bare reserved word -> the role name
    "SELECT session_user FROM dim_date",
    "SELECT current_user FROM dim_date",
])
def test_server_identity_disclosure_rejected(sql):
    for dialect in ("postgres", "duckdb"):
        with pytest.raises(GuardrailError):
            validate_sql(sql, ALLOW, dialect=dialect)
