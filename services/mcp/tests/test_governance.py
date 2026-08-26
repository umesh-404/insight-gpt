"""The governance boundary — what this server refuses, and why that is the point.

These tests are the safety posture written as assertions: there is no way to
send SQL, no way to reach an ungoverned metric, dimension or table, and no way
for an abstention to be quietly turned into a number.
"""

from __future__ import annotations

import json

import pytest
from app.engine.guardrails import GuardrailError, validate_sql
from conftest import invoke, structured, tools
from mcp.server.mcpserver.exceptions import ToolError

from insight_mcp.context import CONTEXT
from insight_mcp.server import SAFETY_POSTURE, TOOL_NAMES

# Parameter names that would betray a raw-SQL or write escape hatch.
_FORBIDDEN_PARAM_HINTS = ("sql", "statement", "raw", "expression", "table", "execute")
_FORBIDDEN_TOOL_HINTS = ("sql", "execute", "run_query", "write", "insert", "update", "delete")


def test_no_tool_accepts_raw_sql() -> None:
    """The inventory itself is the guarantee: nothing takes a SQL string.

    A naive text-to-SQL MCP server exposes one ``run_sql`` tool and trusts the
    model; this one cannot be asked to run arbitrary SQL because no parameter
    anywhere would carry it.
    """
    for tool in tools():
        assert not any(hint in tool.name.lower() for hint in _FORBIDDEN_TOOL_HINTS), tool.name
        properties = (tool.input_schema or {}).get("properties", {})
        for param in properties:
            assert not any(hint in param.lower() for hint in _FORBIDDEN_PARAM_HINTS), (
                f"{tool.name}.{param} looks like a raw-SQL or ungoverned escape hatch"
            )


def test_every_tool_is_annotated_read_only() -> None:
    for tool in tools():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_tool_inventory_is_closed() -> None:
    # A new tool must be a deliberate act: this list is what the docs promise.
    assert sorted(t.name for t in tools()) == sorted(TOOL_NAMES)
    assert len(TOOL_NAMES) == 7


def test_query_metric_rejects_an_unknown_metric() -> None:
    with pytest.raises(ToolError) as exc:
        invoke("query_metric", {"metric": "customer_lifetime_value"})
    message = str(exc.value)
    assert "REJECTED" in message
    assert "unknown metric" in message
    # The rejection names the legal alternatives so the caller can self-correct.
    assert "revenue" in message


def test_query_metric_rejects_an_unknown_dimension() -> None:
    with pytest.raises(ToolError) as exc:
        invoke("query_metric", {"metric": "revenue", "dimensions": ["salesperson"]})
    assert "unknown dimension" in str(exc.value)


def test_query_metric_rejects_a_dimension_the_metric_may_not_be_sliced_by() -> None:
    """``region`` is a real dimension — just not a governed slice of this metric."""
    with pytest.raises(ToolError) as exc:
        invoke("query_metric", {"metric": "units_on_hand", "dimensions": ["region"]})
    message = str(exc.value)
    assert "cannot be sliced by 'region'" in message
    assert "store" in message  # the allowed set is spelled out


def test_query_metric_rejects_sql_smuggled_into_the_metric_name() -> None:
    with pytest.raises(ToolError) as exc:
        invoke("query_metric", {"metric": "revenue; DROP TABLE dim_customer"})
    assert "unknown metric" in str(exc.value)


def test_query_metric_rejects_a_malformed_date_window() -> None:
    with pytest.raises(ToolError) as exc:
        invoke("query_metric", {"metric": "revenue", "start_date": "2026-01-01"})
    assert "must be given together" in str(exc.value)


def test_query_metric_caps_the_limit_at_the_catalog_maximum() -> None:
    body = structured("query_metric", {"metric": "revenue", "dimensions": ["region"],
                                       "limit": 10_000_000})
    assert body["limit"] == CONTEXT.catalog.max_rows
    assert f"LIMIT {CONTEXT.catalog.max_rows}" in body["sql"]


def test_generated_sql_only_touches_allow_listed_tables() -> None:
    body = structured("query_metric", {"metric": "revenue", "dimensions": ["region", "category"]})
    referenced = {word.split(".")[0] for word in body["sql"].replace("\n", " ").split()
                  if word.startswith(("fact_", "dim_"))}
    assert referenced
    assert referenced <= set(CONTEXT.catalog.allow_tables)
    # And the guardrail agrees, parsing the statement rather than reading words.
    validate_sql(body["sql"], set(CONTEXT.catalog.allow_tables), dialect="duckdb")


def test_the_executor_rejects_an_off_allow_list_table() -> None:
    """Defense in depth: even hand-written SQL cannot reach an ungoverned table.

    Every tool bottoms out in this same executor, so this is the floor beneath
    the builder rather than a separate code path.
    """
    with pytest.raises(GuardrailError) as exc:
        CONTEXT.engine.warehouse.run("SELECT * FROM raw_customers LIMIT 1", [])
    assert "non-allow-listed" in str(exc.value)


def test_the_executor_rejects_a_write_statement() -> None:
    with pytest.raises(GuardrailError):
        CONTEXT.engine.warehouse.run("DELETE FROM fact_order_items", [])


def test_ask_propagates_abstention_rather_than_guessing() -> None:
    body = structured("ask", {"question": "What was our customer lifetime value last quarter?"})
    assert body["abstained"] is True
    assert body["route"] == "abstain"
    assert body["abstain_reason"]
    assert body["suggestions"], "an abstention should point at the closest governed metrics"
    # No number is emitted, and the refusal leads the rendered summary.
    assert body["tables"] == []
    assert body["sql"] == []
    assert body["summary"].startswith("ABSTAINED")


def test_ask_rejects_an_empty_question() -> None:
    with pytest.raises(ToolError) as exc:
        invoke("ask", {"question": "   "})
    assert "REJECTED" in str(exc.value)


def test_search_documents_rejects_an_empty_query() -> None:
    with pytest.raises(ToolError):
        invoke("search_documents", {"query": ""})


def test_safety_posture_is_published_through_system_status() -> None:
    body = structured("system_status")
    assert body["safety"] == SAFETY_POSTURE
    blob = json.dumps(body).lower()
    assert "no raw-sql tool" in blob


def test_server_instructions_do_not_invite_ungoverned_answers() -> None:
    from insight_mcp.server import INSTRUCTIONS

    lowered = INSTRUCTIONS.lower()
    assert "governed metric catalog" in lowered
    assert "no raw sql" in lowered


def test_tool_result_is_structured_not_free_text() -> None:
    result = invoke("list_metrics")
    assert result.structured_content is not None
    # The text block mirrors the structured payload, so a text-only client and a
    # structured client cannot be shown different numbers.
    assert json.loads(result.content[0].text) == result.structured_content
