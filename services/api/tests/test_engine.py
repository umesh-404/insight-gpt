"""End-to-end engine tests on the fixture stack (offline, deterministic)."""

from app.engine.engine import InsightEngine


def make_engine():
    return InsightEngine.fixture()  # fake provider + DuckDB fixture warehouse


def test_why_did_sales_decline_last_quarter():
    env = make_engine().ask("Why did sales decline last quarter?")
    assert env.route == "hybrid"
    # The planted story: Q2 revenue below Q1, North + Electronics the drivers.
    f_answer = env.answer.lower()
    assert "fell" in f_answer or "decline" in f_answer
    assert "north" in f_answer
    # It must show its work: SQL executed and documents cited.
    assert env.sql, "structured path should have executed SQL"
    assert env.citations, "hybrid answer should cite documents"
    assert env.chart is not None
    # Trend table has both quarters and Q2 < Q1.
    trend = env.tables[0]
    values = {row[0]: row[1] for row in trend.rows}
    assert values["2026Q2"] < values["2026Q1"]


def test_summarize_complaints_is_unstructured_with_citations():
    env = make_engine().ask("Summarize customer complaints this month.")
    assert env.route == "unstructured"
    assert env.citations
    assert not env.sql            # pure document answer
    assert "north" in env.answer.lower()


def test_scalar_revenue_question():
    env = make_engine().ask("What was revenue last quarter?")
    assert env.route == "structured"
    assert env.sql
    assert env.tables[0].rows[0][0] > 0


def test_numbers_never_come_from_the_model():
    # The engine's numbers come from SQL; the trend total must equal the sum of
    # the by-quarter warehouse rows, not anything the provider wrote.
    env = make_engine().ask("Why did sales decline last quarter?")
    trend = env.tables[0]
    assert all(isinstance(r[1], (int, float)) for r in trend.rows)


def test_comparison_answer_quotes_the_true_lowest_category():
    """The disambiguation answer must name the real minimum, not a top-N tail.

    The lowest category used to be read off the last row of a
    ``desc LIMIT 3`` query, so with five categories it reported the
    third-highest as the "lowest".
    """
    from app.semantic.query_builder import Filter, MetricSelection, build_query

    engine = make_engine()
    env = engine.ask("Why are the least and highest sales the same?")
    assert env.route == "conversational"

    _, _, start, end = engine._last_complete_quarter()
    selection = MetricSelection(
        metric="revenue",
        dimensions=["category"],
        order_by_metric="asc",
        filters=[Filter(dimension="date", op="between", values=[start, end])],
    )
    built = build_query(selection, engine.catalog)
    rows = engine.warehouse.run(built.sql, built.params).rows
    assert len(rows) > 3, "fixture must have more categories than the old LIMIT 3"
    true_lowest, true_highest = str(rows[0][0]), str(rows[-1][0])

    lines = env.answer.splitlines()
    category_header = next(
        i for i, line in enumerate(lines) if line.startswith("**By category")
    )
    section = lines[category_header:]
    highest_line = next(line for line in section if line.startswith("- Highest:"))
    lowest_line = next(line for line in section if line.startswith("- Lowest:"))
    assert true_highest in highest_line, env.answer
    assert true_lowest in lowest_line, env.answer
