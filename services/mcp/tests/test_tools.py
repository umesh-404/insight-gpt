"""Each tool returns the shape its schema promises, against the offline stack."""

from __future__ import annotations

from conftest import structured, tools

from insight_mcp.server import TOOL_NAMES


def test_tool_inventory_is_exactly_the_documented_set() -> None:
    assert sorted(t.name for t in tools()) == sorted(TOOL_NAMES)


def test_every_tool_publishes_an_output_schema() -> None:
    # The published schema *is* the contract this server sells: a client can
    # know the shape of an answer before asking for one.
    for tool in tools():
        assert tool.output_schema, f"{tool.name} publishes no outputSchema"


def test_list_metrics_returns_the_governed_catalog() -> None:
    body = structured("list_metrics")
    keys = {m["key"] for m in body["metrics"]}
    assert {"revenue", "gross_margin_pct", "units_on_hand"} <= keys

    revenue = next(m for m in body["metrics"] if m["key"] == "revenue")
    assert revenue["label"] == "Revenue"
    assert revenue["unit"] == "currency"
    assert revenue["additive"] is True
    assert "region" in revenue["dimensions"]

    ratio = next(m for m in body["metrics"] if m["key"] == "gross_margin_pct")
    assert ratio["additive"] is False

    assert "month" in body["time_grains"]
    assert "fact_order_items" in body["allow_tables"]
    assert body["limits"]["max_rows"] >= body["limits"]["default_rows"]


def test_list_dimensions_marks_the_date_dimension_and_its_grains() -> None:
    body = structured("list_dimensions")
    by_key = {d["key"]: d for d in body["dimensions"]}
    assert {"region", "category", "date"} <= set(by_key)

    date = by_key["date"]
    assert date["is_date"] is True
    assert {"day", "month", "quarter"} <= set(date["grains"])
    assert date["default_grain"] == "month"

    assert by_key["region"]["is_date"] is False
    assert by_key["region"]["table"] == "dim_customer"


def test_query_metric_returns_rows_and_the_sql_that_produced_them() -> None:
    body = structured(
        "query_metric",
        {
            "metric": "revenue",
            "dimensions": ["region"],
            "start_date": "2026-04-01",
            "end_date": "2026-06-30",
            "order": "desc",
            "limit": 10,
        },
    )
    assert body["metric"] == "revenue"
    assert body["row_count"] == len(body["rows"]) > 0
    assert [c["name"] for c in body["columns"]] == ["region", "revenue"]
    assert [c["role"] for c in body["columns"]] == ["dimension", "metric"]

    # The exact statement is returned with its bound parameters — auditable.
    assert body["sql"].startswith("SELECT ")
    assert "GROUP BY" in body["sql"] and "LIMIT" in body["sql"]
    assert body["params"] == ["2026-04-01", "2026-06-30"]

    # Values are JSON primitives, not driver objects.
    assert all(isinstance(row[0], str) for row in body["rows"])
    assert all(isinstance(row[1], int | float) for row in body["rows"])
    # Ordered descending on the metric, as asked.
    assert body["rows"] == sorted(body["rows"], key=lambda r: r[1], reverse=True)


def test_query_metric_resolves_an_alias_to_its_canonical_metric() -> None:
    body = structured("query_metric", {"metric": "aov", "limit": 1})
    assert body["metric"] == "avg_order_value"


def test_explain_metric_explains_definition_source_and_additivity() -> None:
    body = structured("explain_metric", {"metric": "gross_margin_pct"})
    assert body["key"] == "gross_margin_pct"
    assert "SUM(" in body["expression"]
    assert body["fact"] == "fact_order_items"
    assert body["fact_grain"]
    assert body["additive"] is False
    assert "NOT additive" in body["additivity_note"]
    assert {d["key"] for d in body["allowed_dimensions"]} == {
        "date", "region", "category", "product", "channel",
    }
    assert "fact_order_items" in body["tables_touched"]


def test_ask_returns_a_full_answer_envelope() -> None:
    body = structured("ask", {"question": "Why did revenue decline last quarter?"})
    assert body["route"] in ("structured", "hybrid", "unstructured")
    assert body["abstained"] is False
    assert body["answer"]
    assert body["summary"].startswith(body["answer"][:20])
    assert body["sql"], "a structured answer must carry the SQL it ran"
    assert body["tables"]
    assert body["citations"]
    assert body["confidence"] in ("high", "medium", "low")


def test_search_documents_returns_citable_hits() -> None:
    body = structured("search_documents", {"query": "delivery delays", "k": 3})
    assert body["retriever"] == "fixture"
    assert 0 < body["result_count"] <= 3
    first = body["results"][0]
    assert first["doc_id"] and first["title"] and first["excerpt"]
    assert first["n"] == 1


def test_search_documents_applies_the_source_type_filter() -> None:
    body = structured(
        "search_documents", {"query": "delivery delays", "source_type": "ticket", "k": 5}
    )
    assert body["results"], "expected at least one ticket in the sample corpus"
    assert {hit["source_type"] for hit in body["results"]} == {"ticket"}


def test_system_status_reports_backends_without_secrets() -> None:
    body = structured("system_status")
    assert body["status"] == "ok"
    assert body["warehouse"] == {
        "mode": "duckdb-fixture",
        "reachable": True,
        "detail": "in-process DuckDB fixture",
    }
    assert body["retriever"]["mode"] == "fixture"
    assert body["llm"]["provider"] == "fake"
    assert body["catalog"]["metrics"] > 0
    assert sorted(body["tools"]) == sorted(TOOL_NAMES)
    assert body["safety"]

    # No credential value may appear anywhere in the payload.
    blob = repr(body).lower()
    assert "password" not in blob
    assert "api_key" not in blob
    assert "secret" not in blob
