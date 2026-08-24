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
