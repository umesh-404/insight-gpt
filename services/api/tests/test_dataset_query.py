"""A numeric question about an uploaded table: SQL, retrieval, or abstention.

The feature these tests cover is also the one most able to do harm. An upload
that cannot answer "what is the total revenue in it?" is disappointing; an
upload that answers it with a retail figure, or answers it from a sample of its
own rows, is a confident wrong number with nothing on screen marking it wrong.

So the tests come in two halves. The first half asserts the feature works: the
three question shapes that now answer from SQL, and what the reader sees that
tells them so. The second half tries to break it — a file that was never
uploaded, a sum over a text column, an aggregate over a file that only partly
loaded, a question whose column cannot be identified, and both directions of
the provenance confusion (an upload's figure presented as the warehouse's, and
the warehouse's presented as an upload's).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("RETRIEVER", "fixture")

import pytest  # noqa: E402

from app.api import datasets_store  # noqa: E402
from app.engine.dataset_query import (  # noqa: E402
    DatasetDecline,
    DatasetPlan,
    plan_dataset_query,
    resolve_named_file,
)
from app.engine.engine import InsightEngine  # noqa: E402
from app.engine.grounding import detect_aggregate, question_names_a_file  # noqa: E402
from app.warehouse.datasets_db import DatasetsDB, derive_schema, read_table_file  # noqa: E402

SALES = (
    "order_id,order_date,region,revenue,units,note\n"
    "1,2026-04-01,North,100,2,ok\n"
    "2,2026-04-02,South,250,1,late\n"
    "3,2026-05-03,North,50,4,ok\n"
)
RETAIL_TABLES = ("fact_order_items", "dim_date", "dim_product", "dim_store")


@pytest.fixture
def db() -> Iterator[DatasetsDB]:
    database = DatasetsDB(":memory:")
    yield database
    database.close()


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[None]:
    """An isolated dataset registry, so these tests see only their own files."""
    datasets_store.set_data_dir(tmp_path / "ingested")
    datasets_store.reset_state()
    yield
    datasets_store.reset_state()
    datasets_store.set_data_dir(None)


def _upload(tmp_path: Path, name: str = "my_sales.csv", body: str = SALES,
            kind: str = "tabular", status: str = "ready",
            dataset_id: str = "ds_test01") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    datasets_store.put(
        datasets_store.StoredDataset(
            id=dataset_id, name=name, kind=kind, status=status, bytes=len(body),
            chunks=3, created_at=datetime.now(UTC), path=str(path),
        )
    )
    return path


def _engine(db: DatasetsDB) -> InsightEngine:
    engine = InsightEngine.fixture()
    engine.datasets_db = db
    return engine


def _table(tmp_path: Path, max_rows: int | None = None, body: str = SALES,
           file_name: str = "my_sales.csv"):
    path = tmp_path / "t.csv"
    path.write_text(body, encoding="utf-8")
    headers, rows, source = read_table_file(path, max_rows=max_rows or 10_000)
    return derive_schema("ds_test01", file_name, headers, rows, source)


# ==========================================================================
# Filename detection
# ==========================================================================
def test_a_filename_is_extracted_without_swallowing_the_question() -> None:
    """A lazy match anchored at a word boundary started at the first word of
    the sentence, so the "filename" was the whole question and matched nothing
    in the registry."""
    assert question_names_a_file("what is the total revenue in my_sales.csv?") == "my_sales.csv"
    assert question_names_a_file("summarise notes.pdf please") == "notes.pdf"
    assert question_names_a_file('total in "q3 sales.csv"') == "q3 sales.csv"
    assert question_names_a_file("what was revenue last quarter") is None


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("what is the total revenue", "sum"),
        ("average order value", "avg"),
        ("how many rows", "count"),
        ("the highest revenue", "max"),
        ("the lowest revenue", "min"),
        # "summarise" must not read as "sum".
        ("summarise the complaints", None),
        ("what are the main themes", None),
    ],
)
def test_aggregate_detection(question: str, expected: str | None) -> None:
    assert detect_aggregate(question) == expected


# ==========================================================================
# What now answers from SQL
# ==========================================================================
def test_a_total_is_computed_over_every_row(tmp_path: Path, registry: None, db: DatasetsDB) -> None:
    _upload(tmp_path)
    envelope = _engine(db).ask("what is the total revenue in my_sales.csv?")

    assert envelope.abstained is False
    assert "400" in envelope.answer
    assert envelope.tables[0].rows == [[400]]


def test_the_answer_names_the_dataset_and_shows_the_query(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """Honesty rule §3.2: a figure from an upload must be visibly attributed to
    that upload, in the prose, in the SQL, in the citation and in the caveats."""
    _upload(tmp_path)
    envelope = _engine(db).ask("what is the total revenue in my_sales.csv?")

    assert "my_sales.csv" in envelope.answer
    assert "retail warehouse" in envelope.answer  # says which source it is NOT
    assert envelope.sql and "upload_ds_test01" in envelope.sql[0]
    assert [c.source_type for c in envelope.citations] == ["dataset"]
    assert envelope.citations[0].title == "my_sales.csv"
    assert "revenue" in (envelope.citations[0].snippet or "")
    assert any("not from the governed retail warehouse" in c for c in envelope.caveats)
    assert any("Derived schema" in c for c in envelope.caveats)


def test_a_breakdown_groups_over_the_named_column(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    _upload(tmp_path)
    envelope = _engine(db).ask("total revenue by region in my_sales.csv")

    assert envelope.tables[0].rows == [["South", 250], ["North", 150]]
    assert "GROUP BY" in envelope.sql[0]
    assert envelope.chart is not None and envelope.chart.type == "bar"


def test_a_row_count_counts_rows_not_a_column(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    _upload(tmp_path)
    envelope = _engine(db).ask("how many rows are in my_sales.csv")
    assert envelope.tables[0].rows == [[3]]
    assert "COUNT(*)" in envelope.sql[0]


def test_an_average_and_an_extremum(tmp_path: Path, registry: None, db: DatasetsDB) -> None:
    engine = _engine(db)
    _upload(tmp_path)
    assert engine.ask("average revenue in my_sales.csv").tables[0].rows == [[400 / 3]]
    assert engine.ask("maximum revenue in my_sales.csv").tables[0].rows == [[250]]


def test_which_group_leads_is_a_ranking_of_totals_and_says_so(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """"which region has the highest revenue" is a ranking of per-group totals.
    Reading "highest" as a sum is an interpretation, so it is stated."""
    _upload(tmp_path)
    envelope = _engine(db).ask("which region has the highest revenue in my_sales.csv")
    assert "total of revenue by region" in envelope.answer
    assert envelope.tables[0].rows[0] == ["South", 250]


def test_a_currency_column_is_summed_and_the_cleanup_is_reported(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    body = 'region,amount\nNorth,"₹1,250"\nSouth,"₹980"\n'
    _upload(tmp_path, name="money.csv", body=body)
    envelope = _engine(db).ask("what is the total amount in money.csv")
    assert envelope.tables[0].rows == [[2230]]
    assert any("currency symbols" in c for c in envelope.caveats)


def test_the_figure_is_not_dressed_as_rupees(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """The catalog knows a governed metric is rupees; an uploaded column is
    whatever the uploader meant. Stamping the symbol on it would assert
    something unknown and make the figure read like a warehouse one."""
    _upload(tmp_path)
    envelope = _engine(db).ask("what is the total revenue in my_sales.csv?")
    assert "₹" not in envelope.answer


def test_a_value_in_the_question_narrows_the_query_and_says_so(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """A question naming a value asks about part of the table. Summing the whole
    column and labelling it "over all 3 rows" would be true about the
    arithmetic and the wrong answer to the question."""
    _upload(tmp_path)
    envelope = _engine(db).ask("total revenue for North in my_sales.csv")

    assert 'WHERE "region" = ?' in envelope.sql[0]
    assert envelope.tables[0].rows == [[150]]
    assert "where region is 'North'" in envelope.answer
    assert any("covers only the rows where region is 'North'" in c for c in envelope.caveats)


def test_two_values_are_declined_rather_than_half_applied(tmp_path: Path) -> None:
    table = _table(tmp_path)
    decision = plan_dataset_query("total revenue for North and South in my_sales.csv", table)
    assert isinstance(decision, DatasetDecline)
    assert "more than one value" in decision.reason


def test_a_period_is_declined_because_scoping_is_not_built(tmp_path: Path) -> None:
    table = _table(tmp_path)
    decision = plan_dataset_query("total revenue in 2026 in my_sales.csv", table)
    assert isinstance(decision, DatasetDecline)
    assert "scope" in decision.reason and "2026" in decision.reason


def test_a_value_that_is_also_a_column_name_is_not_read_as_a_filter(
    tmp_path: Path,
) -> None:
    """In "total revenue by region" the word names the column, not a value."""
    body = "label,amount\nrevenue,10\nother,5\n"
    table = _table(tmp_path, body=body)
    plan = plan_dataset_query("what is the total amount by label in my_sales.csv", table)
    assert isinstance(plan, DatasetPlan)
    assert "WHERE" not in plan.sql


def test_the_filename_is_not_read_as_a_value(tmp_path: Path) -> None:
    """A file called "q3 sales.csv" contributes the word "sales", which here is
    also a value in a category column. The filename names the file, not a row
    of it, so it is taken out of the question before the question is read."""
    body = "channel,amount\nsales,10\nsupport,5\n"
    table = _table(tmp_path, body=body, file_name="q3 sales.csv")
    plan = plan_dataset_query('what is the total amount in "q3 sales.csv"', table)
    assert isinstance(plan, DatasetPlan)
    assert "WHERE" not in plan.sql
    assert plan.scope == "all 2 rows"


def test_only_low_cardinality_text_columns_get_category_values(tmp_path: Path) -> None:
    table = _table(tmp_path)
    region = table.column("region")
    assert region is not None and sorted(region.categories) == ["North", "South"]
    # A numeric column is not a category column.
    revenue = table.column("revenue")
    assert revenue is not None and revenue.categories == []


# ==========================================================================
# What still answers from retrieval
# ==========================================================================
def test_a_non_numeric_question_about_a_table_still_goes_to_retrieval(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    _upload(tmp_path)
    envelope = _engine(db).ask("what are the main themes in my_sales.csv")
    assert envelope.sql == []
    assert envelope.route != "structured"


def test_evidence_that_is_not_the_named_file_is_flagged_as_such(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """Retrieval is not scoped to the named upload, so a question about a file
    can be answered with citations to other documents. Nothing is fabricated,
    but the envelope must not leave "these documents" to be read as "your
    file"."""
    _upload(tmp_path)
    envelope = _engine(db).ask("what are the main themes in my_sales.csv")
    assert envelope.citations  # the fixture corpus answered
    assert all("my_sales.csv" not in c.title for c in envelope.citations)
    assert any("None of the evidence below comes from" in c for c in envelope.caveats)


def test_a_document_upload_has_no_table_and_goes_to_retrieval(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    _upload(tmp_path, name="policy.pdf", body="text", kind="document")
    envelope = _engine(db).ask("what is the total revenue in policy.pdf")
    assert envelope.sql == []
    assert "upload_" not in str(envelope.sql)


def test_an_unopenable_database_degrades_to_retrieval_rather_than_failing(
    tmp_path: Path, registry: None
) -> None:
    """If the uploaded-table database cannot be opened, the question is still
    answered from the file's indexed text — and still never from the retail
    warehouse."""
    _upload(tmp_path)
    broken = DatasetsDB(tmp_path)  # a directory, not a database file
    match = resolve_named_file("my_sales.csv", broken)
    assert match.known and match.table is None and "could not be opened" in (match.note or "")

    envelope = _engine(broken).ask("what is the total revenue in my_sales.csv")
    assert envelope.sql == []
    assert envelope.route != "structured"


def test_a_dataset_that_is_still_indexing_has_no_table_yet(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    _upload(tmp_path, status="indexing")
    match = resolve_named_file("my_sales.csv", db)
    assert match.known and match.table is None and "not available yet" in (match.note or "")


# ==========================================================================
# What abstains — the honesty half
# ==========================================================================
def test_a_file_that_was_never_uploaded_abstains_and_names_no_figure(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """Honesty rule §3.1. The failure this guards against is a retail figure
    returned in answer to a question about somebody's own file."""
    _upload(tmp_path)  # a *different* file is uploaded
    envelope = _engine(db).ask("what is the total revenue in nowhere.csv?")

    assert envelope.abstained is True and envelope.route == "abstain"
    assert envelope.sql == [] and envelope.tables == []
    assert "nowhere.csv" in envelope.answer
    assert not any(t in envelope.answer for t in RETAIL_TABLES)


def test_an_ungoverned_metric_plus_a_missing_file_abstains_about_the_file(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """The file reference is the stronger signal, so it is read before the
    governed-metric gates. Otherwise "what is the churn in nowhere.csv" is
    refused for naming an ungoverned metric, which is true but not the reason
    the reader needs."""
    _upload(tmp_path)
    envelope = _engine(db).ask("what is the churn rate in nowhere.csv")
    assert envelope.abstained is True
    assert "nowhere.csv" in envelope.answer
    assert "governed metric" not in envelope.answer


def test_an_empty_registry_abstains_rather_than_answering_from_the_warehouse(
    registry: None, db: DatasetsDB
) -> None:
    envelope = _engine(db).ask("what is the total revenue in my_sales.csv?")
    assert envelope.abstained is True
    assert envelope.sql == []


def test_a_sum_over_a_text_column_is_declined_not_coerced(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    _upload(tmp_path)
    envelope = _engine(db).ask("what is the total note in my_sales.csv")
    assert envelope.abstained is True
    assert "read as text" in envelope.answer
    assert "revenue" in envelope.answer  # points at the columns that can be summed


def test_an_aggregate_over_a_partly_loaded_table_is_declined(tmp_path: Path) -> None:
    """Honesty rule §3.4: a total of the rows that fit is not the total."""
    table = _table(tmp_path, max_rows=2)
    assert table.truncated
    decision = plan_dataset_query("what is the total revenue in my_sales.csv", table)
    assert isinstance(decision, DatasetDecline)
    assert "part of the file" in decision.reason


def test_the_engine_declines_a_truncated_table_end_to_end(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    path = _upload(tmp_path)
    db.materialize("ds_test01", "my_sales.csv", path, max_rows=2)
    envelope = _engine(db).ask("what is the total revenue in my_sales.csv")
    assert envelope.abstained is True
    assert envelope.sql == []
    assert "3 rows" in envelope.answer and "first 2" in envelope.answer


def test_an_unidentifiable_column_is_declined_with_the_real_columns(tmp_path: Path) -> None:
    table = _table(tmp_path)
    decision = plan_dataset_query("what is the total in my_sales.csv", table)
    assert isinstance(decision, DatasetDecline)
    assert "order_id" in decision.reason and "revenue" in decision.reason


def test_a_question_with_no_arithmetic_is_not_planned(tmp_path: Path) -> None:
    table = _table(tmp_path)
    assert plan_dataset_query("what does this file contain", table) is None


def test_a_text_extremum_goes_to_retrieval_rather_than_being_sorted(
    tmp_path: Path,
) -> None:
    """Not planned as SQL, and not declined either.

    An extremum over a text column would be an alphabetical ordering, which is
    never the answer to "the highest note" or "the most common complaint". But
    declining is the wrong reply to a reader who never asked for SQL: it is a
    thematic question about a file whose text is indexed, so it belongs to
    retrieval. `None` means "not mine" and the caller routes to the documents.

    The safety property is unchanged and is what makes this allowed: returning
    `None` emits no figure. Anywhere a number would otherwise be invented -
    a sum over text, a total over a truncated table - the decline stands.
    """
    table = _table(tmp_path)
    assert plan_dataset_query("what is the highest note in my_sales.csv", table) is None
    assert plan_dataset_query("the most common note in my_sales.csv", table) is None

    # The declines that matter are still declines: this one would otherwise
    # produce a number, and a wrong one.
    numeric_over_text = plan_dataset_query(
        "what is the total note in my_sales.csv", table
    )
    assert isinstance(numeric_over_text, DatasetDecline)
    assert "text" in numeric_over_text.reason


# ==========================================================================
# Provenance, in both directions
# ==========================================================================
def test_no_file_question_ever_carries_warehouse_sql(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """The single worst failure available to this engine: a retail figure in
    answer to a question about the reader's own file."""
    _upload(tmp_path)
    engine = _engine(db)
    questions = [
        "what is the total revenue in my_sales.csv?",
        "total revenue by region in my_sales.csv",
        "what was revenue last quarter in my_sales.csv",
        "why did sales decline in my_sales.csv",
        "what is the total revenue in nowhere.csv",
        "how many rows are in my_sales.csv",
        "what are the main themes in my_sales.csv",
    ]
    for question in questions:
        envelope = engine.ask(question)
        joined = " ".join(envelope.sql)
        for table in RETAIL_TABLES:
            assert table not in joined, f"{question!r} reached the retail warehouse"
        for citation in envelope.citations:
            assert citation.source_type in ("dataset", "attachment", "report", "ticket",
                                            "review", "email"), citation.source_type


def test_a_governed_question_is_never_answered_from_an_upload(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """And the reverse: a question that names no file stays on the governed
    path, with no uploaded table in its SQL and no dataset in its citations."""
    _upload(tmp_path)
    envelope = _engine(db).ask("what was revenue last quarter")
    joined = " ".join(envelope.sql)
    assert "upload_" not in joined
    assert "fact_order_items" in joined
    assert all(c.source_type != "dataset" for c in envelope.citations)


def test_a_generated_plan_only_ever_names_the_dataset_own_table(tmp_path: Path) -> None:
    table = _table(tmp_path)
    for question in (
        "total revenue in my_sales.csv",
        "average units by region in my_sales.csv",
        "how many rows in my_sales.csv",
        "which region has the highest revenue in my_sales.csv",
    ):
        plan = plan_dataset_query(question, table)
        assert isinstance(plan, DatasetPlan)
        assert plan.sql.count('"') >= 2
        assert f'FROM "{table.table}"' in plan.sql
        for retail in RETAIL_TABLES:
            assert retail not in plan.sql
        assert "LIMIT" in plan.sql


def test_a_hostile_filename_cannot_reach_the_sql(
    tmp_path: Path, registry: None, db: DatasetsDB
) -> None:
    """The table name comes from the dataset id, never from the file name, so a
    crafted upload name has nowhere to land."""
    _upload(tmp_path, name="a_b.csv", dataset_id="ds_safe01")
    envelope = _engine(db).ask('what is the total revenue in a_b.csv')
    assert "upload_ds_safe01" in envelope.sql[0]
    assert "a_b" not in envelope.sql[0]


# ==========================================================================
# The fall-through to retrieval stays inside the named file
# ==========================================================================
def test_the_retrieval_fallback_is_scoped_to_the_named_dataset() -> None:
    """A shape the planner cannot handle must not become a corpus-wide search.

    When the planner declines a shape - a median, a thematic question - the
    engine falls through to retrieval. That is right. Searching the *whole*
    corpus from there is not: "what is the median revenue in quarry_sales.csv"
    came back about fulfilment delays in the North region, citing five retail
    sample documents and not the file that was asked about. The question named
    a file; the search has to stay in it.
    """
    from app.engine.engine import _retrieval_query

    scoped = {"named_dataset_id": "ds_abc123def456", "entities": {}}
    _query, filters = _retrieval_query("median revenue in quarry_sales.csv", scoped, None)
    assert filters.get("dataset_id") == ["ds_abc123def456"], (
        "the fall-through search was not confined to the dataset the question named"
    )

    # The mirror: an ordinary question is not accidentally confined to a
    # dataset, which would quietly hide the corpus from every other question.
    _query, plain = _retrieval_query("why did sales decline?", {"entities": {}}, None)
    assert "dataset_id" not in plain
