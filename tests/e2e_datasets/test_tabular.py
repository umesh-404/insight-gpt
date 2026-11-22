"""Phase-2 claim 2 — a tabular upload answers a numeric question, and the
figure is both correct and attributed.

This is the workstream with the most room to do harm, so this module is built
to make harm fail rather than to demonstrate success. Four attacks, in the
order of how much damage each one does if it lands:

1. **A wrong number.** The expected total is computed from
   ``fixtures/quarry_sales.csv`` in :mod:`helpers`, not pasted in, so an answer
   that is plausible but wrong cannot pass by looking like a revenue figure.
   The fixture's ``revenue`` column collides on purpose with the governed
   metric of the same name.
2. **A figure from the wrong dataset.** Naming a file that was never uploaded
   has to abstain. The failure to catch is not an error page, it is a correct,
   well-formatted retail number returned in answer to a question about a file
   that does not exist.
3. **The two stores touching.** An uploaded table and the retail warehouse must
   never meet — not in a join, not in a fallback, not in an allow-list, and not
   in either direction. A governed question answered from an upload is the same
   lie as an upload's number presented as a warehouse figure (§3.2, §3.3).
4. **A sum of a sample.** The fixture is 600 rows in 33 KB, deliberately larger
   than the handful of chunks a retrieval pass returns. Any whole-table
   aggregate is therefore either computed over the whole table or it is
   invented from a fragment, and §3.4 says the second is not an answer.

The retriever is a stub carrying the **real** text the parser produces for the
fixture, so what is "in the index" is an input rather than an accident, and
``set_sample_only_docs`` reproduces the mid-table chunk that a real 600-row CSV
splits into — the chunk with no summary line and no way to total anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="needs the services/api environment")
pytest.importorskip("sqlglot", reason="needs the services/api environment")

from app.api import datasets_store as store  # noqa: E402
from app.api.deps import get_engine, reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.engine.engine import InsightEngine  # noqa: E402
from app.engine.retrieval import RetrievedDoc  # noqa: E402
from app.providers.factory import get_provider  # noqa: E402
from app.semantic.catalog import load_catalog  # noqa: E402
from app.warehouse.executor import DuckDBWarehouse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from .helpers import (  # noqa: E402
    ANALYST,
    API,
    LEAKY_FILE,
    NEVER_UPLOADED_FILE,
    QUARRY_FILE,
    QUARRY_MAX_REVENUE,
    QUARRY_PINNED,
    QUARRY_REVENUE_BY_REGION,
    QUARRY_ROWS,
    QUARRY_SAMPLE_PROOF_REGION,
    QUARRY_TOTAL_REVENUE,
    QUARRY_TOTAL_UNITS,
    SECRET_BODIES,
    absolute_numbers,
    ask,
    auth,
    citation_mentions,
    figures_not_in,
    fixture_bytes,
    login,
    numbers_in_evidence,
    poll_status,
    reported_figures,
    states_figure,
    upload,
)

#: Physical tables of the governed warehouse. None of them may appear in a
#: query generated for an uploaded dataset, in either direction (§3.3).
RETAIL_TABLES = (
    "fact_order_items",
    "fact_inventory_snapshot",
    "dim_date",
    "dim_product",
    "dim_customer",
    "dim_store",
    "dim_channel",
)


def test_the_fixture_still_holds_the_numbers_this_module_asserts() -> None:
    """Pinned as well as computed. An edited fixture must break loudly here
    rather than quietly move every expectation in the file."""
    assert QUARRY_PINNED["rows"] == QUARRY_ROWS
    assert pytest.approx(QUARRY_PINNED["total_revenue"], abs=0.005) == QUARRY_TOTAL_REVENUE
    assert QUARRY_PINNED["total_units"] == QUARRY_TOTAL_UNITS
    assert pytest.approx(QUARRY_PINNED["max_revenue"], abs=0.005) == QUARRY_MAX_REVENUE
    for region in ("Fjerda", "Ketterdam", "Novyi Zem", "Shu Han"):
        assert QUARRY_REVENUE_BY_REGION[region] == pytest.approx(
            QUARRY_PINNED[region], abs=0.005
        )


# --------------------------------------------------------------------------
# The real parsed text, and the fragment a mid-table chunk would carry
# --------------------------------------------------------------------------
QUARRY_TEXT, QUARRY_KIND = store.parse_to_text(QUARRY_FILE, fixture_bytes(QUARRY_FILE))


def test_a_600_row_csv_is_recognised_as_a_table() -> None:
    assert QUARRY_KIND == "tabular", (
        f"the fixture parsed as {QUARRY_KIND!r}; everything below is about the tabular "
        "path and would be testing the wrong branch"
    )


def _mid_table_fragment() -> str:
    """The parsed text with its summary line removed.

    A real 600-row CSV is chunked, and only the first chunk carries the
    ``Summary Statistics`` line the parser computes over the whole file. Every
    other chunk looks like this: a window of rows and no totals. Retrieval
    returning one of them is the ordinary case, not a contrived one.
    """
    kept = [
        line
        for line in QUARRY_TEXT.splitlines()
        if not line.startswith("Summary Statistics")
    ]
    return "\n".join(kept)


class StubRetriever:
    """A retriever whose index contents the test states."""

    def __init__(self) -> None:
        self.docs: list[RetrievedDoc] = []
        self.searches: list[tuple[str, dict | None]] = []

    def search(self, query: str, *, filters: dict | None = None, k: int = 5):
        self.searches.append((query, filters))
        return list(self.docs[:k])

    def _doc(self, body: str) -> RetrievedDoc:
        return RetrievedDoc(
            doc_id="ds_quarry000001::0",
            source_type="dataset",
            title=QUARRY_FILE,
            body=body,
            date="2026-11-22",
            score=0.93,
            metadata={"dataset_id": "ds_quarry000001", "dataset_name": QUARRY_FILE},
        )

    def set_quarry_docs(self) -> None:
        """What the first chunk of the uploaded file really contains."""
        self.docs = [self._doc(QUARRY_TEXT)]

    def set_sample_only_docs(self) -> None:
        """A mid-table chunk: rows, no totals. The §3.4 condition."""
        self.docs = [self._doc(_mid_table_fragment())]

    def set_retail_corpus_docs(self) -> None:
        """The retail sample corpus, and nothing of the upload.

        This is the ordinary state of a real index: ``insightgpt_documents``
        holds the seeded retail documents alongside whatever has been uploaded,
        and a question naming a file competes with all of them for the top five.
        """
        self.docs = [
            RetrievedDoc(
                doc_id="REPORT-2026-Q2",
                source_type="docs",
                title="quarterly_sales.csv",
                body=(
                    "Fulfilment delays and late deliveries dominated the quarter, "
                    "especially in the North region. Revenue reached 48.9Cr."
                ),
                date="2026-06-30",
                score=0.88,
                metadata={},
            ),
            RetrievedDoc(
                doc_id="TICKET-55121",
                source_type="docs",
                title="support_notes.md",
                body="Customers in the North region report late deliveries.",
                date="2026-06-02",
                score=0.81,
                metadata={},
            ),
        ]

    def set_empty(self) -> None:
        self.docs = []


@pytest.fixture(scope="module")
def retriever() -> StubRetriever:
    return StubRetriever()


@pytest.fixture(scope="module")
def client(
    retriever: StubRetriever, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    uploads = tmp_path_factory.mktemp("tabular")
    catalog = load_catalog()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RETRIEVER", "qdrant")
        # §2, for the SECOND store phase 2 adds. Uploaded tables are
        # materialized into a DuckDB database that defaults to
        # ``data/uploads/datasets.duckdb`` in the repo — the maintainer's own.
        # Without this the suite writes ``upload_ds_*`` tables into it on every
        # run and never takes them out again, which is how a demo store fills
        # up with a test fixture.
        mp.setenv("DATASETS_DB_PATH", str(uploads / "datasets.duckdb"))
        from app.warehouse.datasets_db import DatasetsDB, set_datasets_db

        scratch_db = DatasetsDB(uploads / "datasets.duckdb")
        set_datasets_db(scratch_db)
        mp.setattr(store, "index_dataset_text", lambda **kw: 12)
        mp.setattr(store, "delete_dataset_points", lambda dataset_id: 12)
        store.set_data_dir(uploads)
        store.reset_state()
        reset_caches()
        app = create_app()
        app.dependency_overrides[get_engine] = lambda: InsightEngine(
            catalog=catalog,
            warehouse=DuckDBWarehouse(allow_tables=set(catalog.allow_tables)),
            retriever=retriever,
            provider=get_provider("fake"),
        )
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()
        store.reset_state()
        store.set_data_dir(None)
        # Close and unhook the scratch database. Only this suite's own tables
        # ever existed in it, and the file goes with ``tmp_path``.
        scratch_db.close()
        set_datasets_db(None)


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> str:
    return login(client, *ANALYST)


@pytest.fixture(scope="module")
def uploaded(client: TestClient, analyst: str) -> dict[str, Any]:
    """The fixture table, uploaded through the real route."""
    response = upload(
        client, analyst, [(QUARRY_FILE, fixture_bytes(QUARRY_FILE))], content_type="text/csv"
    )
    assert response.status_code == 202, response.text
    dataset_id = response.json()["dataset_id"]
    final = poll_status(client, analyst, dataset_id)
    assert final["status"] == "ready", final
    return client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()


# --------------------------------------------------------------------------
# Shared assertions
# --------------------------------------------------------------------------
def _declined(envelope: dict[str, Any]) -> bool:
    """True when the answer declines rather than reports a figure.

    Either the envelope says so, or the prose says it could not do it and
    carries no figure at all. A sentence of apology followed by a number is not
    a decline (§3.4).

    Years are excluded from "carries a figure" deliberately: an honest decline
    quotes the period it could not scope to ("'2026' asks me to"), and counting
    that as a reported figure would classify the most careful refusal in the
    product as an answer.
    """
    if envelope.get("abstained"):
        return True
    answer = (envelope.get("answer") or "").lower()
    hedged = any(
        phrase in answer
        for phrase in (
            "couldn't", "could not", "cannot", "can't", "don't have", "do not have",
            "not able", "unable", "no data", "only part", "partial", "not enough",
        )
    )
    figures = [
        value
        for value in reported_figures(envelope.get("answer") or "")
        if not (1900 <= value <= 2100)
    ]
    return hedged and not figures


def _assert_no_retail_table_in_sql(envelope: dict[str, Any], *, because: str) -> None:
    joined = " ".join(envelope.get("sql") or []).lower()
    for table in RETAIL_TABLES:
        assert table not in joined, (
            f"{because}: the query touches the governed warehouse table {table!r}. "
            f"An uploaded table and the retail warehouse share no query, no join and no "
            f"fallback (§3.3).\nsql={envelope.get('sql')}"
        )


def _assert_attributed_to_the_upload(envelope: dict[str, Any]) -> None:
    """§3.2. A figure from an upload has to be readable AS a figure from that
    upload: the dataset is named, and if SQL produced it the query is shown."""
    answer = envelope.get("answer") or ""
    names_it = QUARRY_FILE.lower() in answer.lower() or citation_mentions(
        envelope, QUARRY_FILE
    )
    assert names_it, (
        "a figure computed from an uploaded table named neither the dataset in the "
        "answer nor in a citation, so the reader cannot tell it is not a warehouse "
        f"number (§3.2).\nanswer={answer!r}\ncitations={envelope.get('citations')}"
    )
    if envelope.get("sql"):
        _assert_no_retail_table_in_sql(
            envelope, because="a question about an uploaded table"
        )
        assert any(
            "ds_" in statement.lower() or QUARRY_FILE.split(".")[0] in statement.lower()
            for statement in envelope["sql"]
        ), (
            "SQL ran for a question about an uploaded file but names no dataset table, "
            f"so it cannot have queried the upload: {envelope['sql']}"
        )


# --------------------------------------------------------------------------
# Attack 1 — the number has to be the right one
# --------------------------------------------------------------------------
def test_a_numeric_question_over_an_uploaded_table_returns_the_true_total(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """The headline claim, with the expected value computed from the file.

    ₹686,566.26 is the sum of all 600 ``revenue`` cells. Nothing else is a pass:
    not a near miss, not a figure from the retail warehouse, and not a total of
    the rows that happened to be retrieved.
    """
    retriever.set_quarry_docs()
    envelope = ask(client, analyst, f"What is the total revenue in {QUARRY_FILE}?")

    if _declined(envelope):
        pytest.fail(
            "the system declined a whole-table aggregate it has the means to compute. "
            "Declining is honest but it is not the phase-2 claim; the figure is "
            f"{QUARRY_TOTAL_REVENUE:,.2f}.\nanswer={envelope['answer']!r}"
        )
    assert states_figure(envelope["answer"], QUARRY_TOTAL_REVENUE), (
        f"the answer does not state the true total {QUARRY_TOTAL_REVENUE:,.2f}. "
        f"Figures it did report: {reported_figures(envelope['answer'])}.\n"
        f"answer={envelope['answer']!r}"
    )
    _assert_attributed_to_the_upload(envelope)


def test_the_total_is_not_accompanied_by_figures_from_nowhere(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """§3.8. A correct total beside an invented subtotal is still a fabrication,
    and the invented one is what a reader quotes in a slide."""
    retriever.set_quarry_docs()
    envelope = ask(client, analyst, f"What is the total revenue in {QUARRY_FILE}?")
    if _declined(envelope):
        pytest.skip("declined; there is no figure to check for company")

    allowed = [
        QUARRY_TOTAL_REVENUE,
        QUARRY_TOTAL_UNITS,
        QUARRY_MAX_REVENUE,
        float(QUARRY_ROWS),
        *QUARRY_REVENUE_BY_REGION.values(),
        *(float(n) for n in numbers_in_evidence(envelope, QUARRY_TEXT)),
    ]
    stray = figures_not_in(envelope["answer"], allowed)
    assert not stray, (
        f"the answer reports {stray}, which is neither a value in the uploaded file nor "
        f"in any table or citation it returned.\nanswer={envelope['answer']!r}"
    )


def test_a_count_question_over_the_upload_reports_the_real_row_count(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """600, not the 50 rows a chunk shows. A row count read off the sample is
    the smallest version of the §3.4 failure and the easiest to miss."""
    retriever.set_quarry_docs()
    envelope = ask(client, analyst, f"How many rows are in {QUARRY_FILE}?")
    if _declined(envelope):
        pytest.skip("declined, which is honest")
    assert states_figure(envelope["answer"], float(QUARRY_ROWS), tolerance=0.0), (
        f"the answer does not report {QUARRY_ROWS} rows; it reported "
        f"{reported_figures(envelope['answer'])}.\nanswer={envelope['answer']!r}"
    )


# --------------------------------------------------------------------------
# Attack 2 — a dataset that was never uploaded
# --------------------------------------------------------------------------
def test_a_numeric_question_about_a_file_never_uploaded_abstains(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    """§3.1. The dangerous outcome is not an error, it is a correct-looking
    retail figure returned for a file that does not exist."""
    retriever.set_empty()
    envelope = ask(
        client, analyst, f"What is the total revenue in {NEVER_UPLOADED_FILE}?"
    )

    assert _declined(envelope), (
        f"a question naming {NEVER_UPLOADED_FILE}, which was never uploaded, came back "
        f"with an answer instead of a decline.\nanswer={envelope['answer']!r}\n"
        f"sql={envelope.get('sql')}"
    )
    assert not envelope.get("sql"), (
        "a question about a non-existent file ran governed SQL over the retail "
        f"warehouse: {envelope['sql']}"
    )
    assert envelope.get("citations") == [], (
        f"an unknown file produced citations: {envelope['citations']}"
    )


def test_a_question_about_an_unknown_file_never_borrows_the_uploaded_one(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """Harder: the index is not empty, it holds a DIFFERENT table whose columns
    match the question perfectly. Answering from the nearest table is how a
    figure gets attached to the wrong file."""
    retriever.set_quarry_docs()
    envelope = ask(
        client, analyst, f"What is the total revenue in {NEVER_UPLOADED_FILE}?"
    )

    if _declined(envelope):
        return
    assert not states_figure(envelope["answer"], QUARRY_TOTAL_REVENUE), (
        f"the total from {QUARRY_FILE} was returned as the answer to a question about "
        f"{NEVER_UPLOADED_FILE}. The reader asked about a file that does not exist and "
        f"was given another file's number.\nanswer={envelope['answer']!r}"
    )
    assert NEVER_UPLOADED_FILE.lower() not in envelope["answer"].lower() or not reported_figures(
        envelope["answer"]
    ), (
        f"a figure is presented as {NEVER_UPLOADED_FILE}'s: "
        f"{reported_figures(envelope['answer'])}"
    )


def test_an_unknown_file_does_not_fall_back_to_the_governed_warehouse(
    client: TestClient, analyst: str, retriever: StubRetriever
) -> None:
    """§3.1/§3.3. The fallback is the thing: a file-shaped question that finds
    nothing must not be retried as a governed metric question."""
    retriever.set_empty()
    for question in (
        f"What is the total revenue in {NEVER_UPLOADED_FILE}?",
        f"Show me revenue by region from {NEVER_UPLOADED_FILE}",
        f"What was the gross margin in {NEVER_UPLOADED_FILE} last quarter?",
    ):
        envelope = ask(client, analyst, question)
        _assert_no_retail_table_in_sql(
            envelope, because=f"{question!r} named a file that does not exist"
        )
        assert not envelope.get("sql"), (
            f"{question!r} fell back to governed SQL: {envelope['sql']}"
        )


# --------------------------------------------------------------------------
# Attack 3 — the two stores must never meet, in either direction
# --------------------------------------------------------------------------
def test_a_governed_question_is_not_answered_from_an_upload(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """§3.2, the reverse direction. The uploaded table has a ``revenue`` column
    and is sitting in the index; a governed question must still be answered by
    the warehouse and must not cite the upload as its source."""
    retriever.set_quarry_docs()
    envelope = ask(client, analyst, "What was revenue last quarter?")

    assert envelope.get("sql"), (
        "a governed metric question did not query the warehouse; if it was answered "
        f"from the uploaded table instead, that is §3.2.\nanswer={envelope['answer']!r}"
    )
    assert not states_figure(envelope["answer"], QUARRY_TOTAL_REVENUE), (
        f"a governed revenue question reported {QUARRY_TOTAL_REVENUE:,.2f} — the total "
        f"of the uploaded {QUARRY_FILE}, presented as a warehouse figure"
    )
    assert not citation_mentions(envelope, QUARRY_FILE), (
        f"a warehouse figure was attributed to the uploaded {QUARRY_FILE}: "
        f"{envelope['citations']}"
    )


def test_an_uploaded_region_name_does_not_enter_a_governed_query(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """The fixture's regions (Fjerda, Ketterdam...) exist in no retail table.

    A governed query filtered by one of them would be a value crossing from the
    upload into the warehouse, and would return zero rows — reported as "no
    revenue in Fjerda", which reads as a fact about the business.
    """
    retriever.set_quarry_docs()
    envelope = ask(client, analyst, "What was revenue in Fjerda last quarter?")
    joined = " ".join(envelope.get("sql") or [])
    assert "fjerda" not in joined.lower(), (
        f"a value from the uploaded table was filtered on in a governed query: {joined}"
    )


def test_the_retail_warehouse_refuses_a_dataset_table(client: TestClient) -> None:
    """§3.3 at the guardrail. The retail allow-list is the warehouse's, and a
    ``ds_*`` table is not in it; the executor must refuse rather than resolve."""
    from app.engine.guardrails import validate_sql

    catalog = load_catalog()
    allow = set(catalog.allow_tables)
    assert not any(t.startswith("ds_") for t in allow), (
        f"the governed allow-list contains an uploaded dataset table: {sorted(allow)}"
    )
    from app.engine.guardrails import GuardrailError

    with pytest.raises(GuardrailError) as raised:
        validate_sql("SELECT SUM(revenue) FROM ds_quarry000001", allow, dialect="duckdb")
    assert "ds_quarry000001" in str(raised.value) or "allow" in str(raised.value).lower()


def test_the_retail_executor_cannot_see_an_uploaded_table(client: TestClient) -> None:
    """Defence in depth: not just refused by the allow-list, but absent from the
    connection. Two databases, two connections (§3.3).

    The allow-list is deliberately widened here so the refusal has to come from
    the database rather than from the guardrail that the test above covers.
    """
    catalog = load_catalog()
    warehouse = DuckDBWarehouse(allow_tables=set(catalog.allow_tables) | {"ds_quarry000001"})
    with pytest.raises(Exception, match="ds_quarry000001"):
        warehouse.run("SELECT * FROM ds_quarry000001 LIMIT 1", [])


# --------------------------------------------------------------------------
# Attack 4 — a sum of a sample is not an answer
# --------------------------------------------------------------------------
def test_a_whole_table_aggregate_is_declined_when_only_a_sample_is_available(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """§3.4, the central case.

    The retrieved chunk is a window of rows with no totals — what every chunk
    after the first looks like for a 600-row CSV. The honest replies are the
    true total (computed over the table, not the chunk) or a decline. A figure
    that is neither is a sum of a sample presented as a total.
    """
    retriever.set_sample_only_docs()
    envelope = ask(client, analyst, f"What is the total revenue in {QUARRY_FILE}?")

    if _declined(envelope):
        return
    if states_figure(envelope["answer"], QUARRY_TOTAL_REVENUE):
        return  # computed over the whole table — the other honest outcome
    pytest.fail(
        "a total was reported from a chunk that contains neither the total nor the "
        f"rows to compute it. Reported: {reported_figures(envelope['answer'])}; the "
        f"true total is {QUARRY_TOTAL_REVENUE:,.2f}.\nanswer={envelope['answer']!r}"
    )


def test_a_grouped_aggregate_is_not_estimated_from_the_visible_rows(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """§3.4, the sharpest probe this fixture allows.

    A region subtotal appears in no summary line the parser emits and cannot be
    derived from the ~50 rows a chunk carries, so there are exactly two honest
    answers: ₹193,651.46 for Fjerda, computed over all 600 rows, or a decline.
    Any other figure was estimated, and it will look entirely credible.
    """
    region = QUARRY_SAMPLE_PROOF_REGION
    expected = QUARRY_REVENUE_BY_REGION[region]
    retriever.set_quarry_docs()
    envelope = ask(
        client, analyst, f"What is the total revenue for {region} in {QUARRY_FILE}?"
    )

    if _declined(envelope):
        return
    assert states_figure(envelope["answer"], expected), (
        f"the {region} subtotal is {expected:,.2f} over all {QUARRY_ROWS} rows. The "
        f"answer reports {reported_figures(envelope['answer'])}, which is not that "
        f"number and is not available from the retrieved chunk either — it was "
        f"estimated.\nanswer={envelope['answer']!r}"
    )
    _assert_attributed_to_the_upload(envelope)


def test_an_average_over_the_upload_is_not_a_sample_average(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """A mean is where a sample hides best: it stays in the right order of
    magnitude, so nothing looks wrong."""
    expected = QUARRY_TOTAL_REVENUE / QUARRY_ROWS
    retriever.set_sample_only_docs()
    envelope = ask(
        client, analyst, f"What is the average order revenue in {QUARRY_FILE}?"
    )
    if _declined(envelope):
        return
    assert states_figure(envelope["answer"], expected, tolerance=0.01), (
        f"the true mean over {QUARRY_ROWS} rows is {expected:,.2f}; the answer reports "
        f"{reported_figures(envelope['answer'])} from a chunk holding a fraction of the "
        f"table.\nanswer={envelope['answer']!r}"
    )


def test_an_answer_from_the_upload_quotes_no_number_it_was_not_given(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """The general form, inherited from phase 1: every absolute number in the
    prose has to be traceable to the file, a returned table or a citation."""
    retriever.set_quarry_docs()
    for question in (
        f"Summarize {QUARRY_FILE}",
        f"Which region leads in {QUARRY_FILE}?",
        f"What is the largest single order in {QUARRY_FILE}?",
    ):
        envelope = ask(client, analyst, question)
        fabricated = absolute_numbers(envelope["answer"]) - numbers_in_evidence(
            envelope, QUARRY_TEXT
        )
        assert not fabricated, (
            f"{question!r}: the answer quotes {sorted(fabricated)}, which appears "
            f"neither in the uploaded file nor in any table or citation it returned"
        )


# --------------------------------------------------------------------------
# The upload's own metadata must be true
# --------------------------------------------------------------------------
def test_the_uploaded_table_reports_its_real_size(uploaded: dict[str, Any]) -> None:
    assert uploaded["kind"] == "tabular", uploaded
    assert uploaded["bytes"] == len(fixture_bytes(QUARRY_FILE)), uploaded


def test_a_tabular_upload_does_not_claim_a_schema_it_did_not_derive(
    uploaded: dict[str, Any],
) -> None:
    """§3.8. Columns and types are reported, never guessed silently. If the
    wire shape grows a schema it must describe the file that was uploaded."""
    schema = uploaded.get("schema") or uploaded.get("columns")
    if schema is None:
        pytest.skip("the dataset wire shape carries no schema yet")
    rendered = str(schema).lower()
    for column in ("order_id", "order_date", "region", "product", "units", "revenue"):
        assert column in rendered, (
            f"the reported schema omits {column!r}, which is in the file: {schema}"
        )


def test_a_truncated_breakdown_is_not_called_the_full_breakdown(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """§3.4. 50 of 600 groups is a sample, and has to be named as one."""
    retriever.set_quarry_docs()
    envelope = ask(
        client, analyst, f"What is the total revenue by order_id in {QUARRY_FILE}?"
    )
    if _declined(envelope):
        return
    tables = envelope.get("tables") or []
    if not tables:
        pytest.skip("no breakdown table was returned")
    shown = len(tables[0].get("rows") or [])
    if shown >= QUARRY_ROWS:
        return  # nothing was truncated, so there is nothing to disclose
    prose = (envelope.get("answer") or "") + " " + " ".join(envelope.get("caveats") or [])
    assert "full breakdown" not in prose.lower() or str(shown) in prose, (
        f"{shown} of {QUARRY_ROWS} groups were returned and the answer calls it the "
        f"full breakdown.\nanswer={envelope['answer']!r}\ncaveats={envelope.get('caveats')}"
    )
    assert any(
        marker in prose.lower() for marker in ("top ", "first ", str(shown), "of 600", "truncat")
    ), (
        f"the breakdown shows {shown} of {QUARRY_ROWS} groups and nothing in the answer "
        f"or its caveats says so.\ncaveats={envelope.get('caveats')}"
    )


def test_three_differently_scoped_questions_do_not_get_one_number(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """A filter in the question has to change the figure, or be refused."""
    retriever.set_quarry_docs()
    answers = {
        question: ask(client, analyst, question)
        for question in (
            f"What is the total revenue in {QUARRY_FILE}?",
            f"What is the total revenue for Fjerda in {QUARRY_FILE}?",
            f"What is the total revenue in {QUARRY_FILE} for orders in March 2026?",
        )
    }
    whole_table = [
        question
        for question, envelope in answers.items()
        if states_figure(envelope["answer"], QUARRY_TOTAL_REVENUE)
    ]
    assert len(whole_table) <= 1, (
        "the whole-table total was returned for "
        f"{len(whole_table)} differently scoped questions:\n"
        + "\n".join(f"  {q}\n    -> {answers[q]['sql']}" for q in whole_table)
    )


# --------------------------------------------------------------------------
# §3.5 — redaction runs before anything is STORED, not only before embedding
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def leaky(client: TestClient, analyst: str) -> dict[str, Any]:
    """A table whose cells hold an API key, a live secret key and an email."""
    response = upload(
        client, analyst, [(LEAKY_FILE, fixture_bytes(LEAKY_FILE))], content_type="text/csv"
    )
    assert response.status_code == 202, response.text
    dataset_id = response.json()["dataset_id"]
    assert poll_status(client, analyst, dataset_id)["status"] == "ready"
    return client.get(f"{API}/datasets/{dataset_id}", headers=auth(analyst)).json()


def test_the_leaky_fixture_is_reported_as_redacted(leaky: dict[str, Any]) -> None:
    """The precondition for the two tests below, and the reason they matter.

    The dataset row says secrets were removed. Everything after this is about
    whether that claim survives contact with the SQL path.
    """
    assert leaky["redactions"] >= 3, (
        f"the redactor found {leaky['redactions']} secrets in a file that contains an "
        "API key, a live secret key and two email addresses; the tests below assume it "
        "found them"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT, the most serious of the round: app/warehouse/datasets_db.py::"
        "DatasetsDB.materialize (line 497) calls read_table_file(path) (line 187) on "
        "the RAW persisted upload and never calls app.api.datasets_store.redact — "
        "'redact' does not appear anywhere in datasets_db.py or dataset_query.py. "
        "Contract §3.5 is 'redaction runs before anything is embedded OR STORED', and "
        "the uploaded-table database is storage. Verified on disk: the bytes of "
        "data/uploads/datasets.duckdb contain 'AIzaFAKE...', 'sk_live_FAKE...' and "
        "'harbour-ops@example.com' verbatim, in a file that .gitignore does not cover. "
        "The embedding path is clean (phase 1 proved it), so the dataset row truthfully "
        "reports 10 redactions while a second store holds all ten values — which makes "
        "this §3.8 as well: the reported count describes work the other path undid."
    ),
)
def test_the_uploaded_table_does_not_store_raw_secrets(
    leaky: dict[str, Any], client: TestClient
) -> None:
    """§3.5. The table is storage, so the redactor has to run before it too."""
    from app.warehouse.datasets_db import get_datasets_db

    table = get_datasets_db().schema_of(leaky["id"])
    assert table is not None, "the tabular upload was not materialized at all"
    rows = get_datasets_db().run(
        leaky["id"], f'SELECT * FROM "{table.table}" LIMIT 100', []
    )
    blob = " ".join(str(cell) for row in rows.rows for cell in row)
    stored = [secret for secret in SECRET_BODIES if secret in blob]
    assert not stored, (
        f"the uploaded-table store holds {len(stored)} unredacted secret(s)/PII value(s) "
        f"verbatim: {stored}"
    )


@pytest.mark.parametrize("column", ["api_key", "customer_email"])
def test_a_grouped_query_does_not_hand_back_a_secret(
    client: TestClient, analyst: str, retriever: StubRetriever, leaky: dict[str, Any], column: str
) -> None:
    """§3.5. Grouping by a redacted column must not undo the redaction."""
    retriever.set_empty()
    envelope = ask(
        client, analyst, f"What is the total revenue by {column} in {LEAKY_FILE}?"
    )
    rendered = str(envelope)
    leaked = [secret for secret in SECRET_BODIES if secret in rendered]
    assert not leaked, (
        f"grouping by {column!r} returned {len(leaked)} secret(s) to the client: "
        f"{leaked}\nanswer={envelope['answer']!r}\ntables={envelope.get('tables')}"
    )


def test_deleting_a_dataset_removes_its_uploaded_table(
    client: TestClient, analyst: str
) -> None:
    """§3.8. ``deleted: true`` has to mean deleted, in both stores."""
    from app.warehouse.datasets_db import get_datasets_db

    response = upload(
        client, analyst, [(QUARRY_FILE, fixture_bytes(QUARRY_FILE))], content_type="text/csv"
    )
    dataset_id = response.json()["dataset_id"]
    assert poll_status(client, analyst, dataset_id)["status"] == "ready"
    # Materialize it the way a question would.
    ask(client, analyst, f"What is the total revenue in {QUARRY_FILE}?")
    assert get_datasets_db().schema_of(dataset_id) is not None, (
        "precondition: the upload was never materialized, so there is no table to leak"
    )

    deleted = client.delete(f"{API}/datasets/{dataset_id}", headers=auth(analyst))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True

    assert get_datasets_db().schema_of(dataset_id) is None, (
        f"the API reported the dataset deleted and left its table "
        f"'upload_{dataset_id}' in the uploaded-datasets database, with its rows"
    )


# --------------------------------------------------------------------------
# §3.1 — the fallback when the SQL path cannot plan a query
# --------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT, and the most serious one in phase 2: app/engine/engine.py lines "
        "187-199 fall back to retrieval when _named_file_envelope returns None (an "
        "aggregate the planner does not support, e.g. a median), and "
        "_retrieval_query (engine.py, the filters dict) never adds a dataset scope - "
        "it only ever sets date_range, region and category. The search therefore runs "
        "over the WHOLE index, so a question naming an uploaded file is answered from "
        "the retail sample corpus. Reproduced live against the real collection: "
        "'What is the median revenue in quarry_sales.csv?' returned 'fulfilment delays "
        "and late deliveries, especially in the North region [1][2][3][4][5]. "
        "Representative items: quarterly_sales.csv; quarterly_sales.csv; "
        "support_notes.md' - five citations, every one of them retail fixture data, and "
        "the file the reader asked about mentioned nowhere. That is §3.1 exactly. The "
        "fix is a dataset filter on the fallback search, not a change to the planner."
    ),
)
def test_a_named_file_question_is_never_answered_from_the_retail_corpus(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """§3.1. The index holds the retail corpus and not this upload's chunks.

    A median is the trigger because the planner does not support it, which is
    fine — the dishonest part is what happens next. The honest outcomes are a
    decline or an answer about the file; an answer about somebody else's
    documents, carrying their citations, is neither.
    """
    retriever.set_retail_corpus_docs()
    envelope = ask(client, analyst, f"What is the median revenue in {QUARRY_FILE}?")

    if _declined(envelope):
        return
    retail_sources = [
        citation
        for citation in envelope.get("citations") or []
        if not citation_mentions({"citations": [citation]}, QUARRY_FILE)
    ]
    assert not retail_sources, (
        f"a question naming {QUARRY_FILE} was answered from documents that are not it: "
        f"{[c.get('title') for c in retail_sources]}.\nanswer={envelope['answer']!r}"
    )


def test_the_fallback_search_for_a_named_file_is_scoped_to_that_dataset(
    client: TestClient, analyst: str, retriever: StubRetriever, uploaded: dict[str, Any]
) -> None:
    """The filter is what makes the fallback safe, so assert the filter."""
    retriever.set_retail_corpus_docs()
    retriever.searches.clear()
    ask(client, analyst, f"What is the median revenue in {QUARRY_FILE}?")

    assert retriever.searches, "the question did not reach retrieval at all"
    _, filters = retriever.searches[-1]
    scoped = bool(filters) and any(
        key in filters for key in ("dataset_id", "dataset_name", "dataset", "source_type")
    )
    assert scoped, (
        f"retrieval for a question naming {QUARRY_FILE} ran with filters={filters}, so "
        "it searched every document in the collection rather than that dataset's"
    )


# --------------------------------------------------------------------------
# §3.3 at the source — the two stores, asserted against the real mechanism
# --------------------------------------------------------------------------
def _dataset_sql_modules() -> tuple[Any, Any] | None:
    try:
        from app.engine import dataset_query
        from app.warehouse import datasets_db
    except ImportError:
        return None
    return datasets_db, dataset_query


_SQL_MECHANISM = _dataset_sql_modules()
needs_dataset_sql = pytest.mark.skipif(
    _SQL_MECHANISM is None,
    reason=(
        "not built yet: app/warehouse/datasets_db.py and app/engine/dataset_query.py "
        "(the per-dataset DuckDB store and its query path) do not exist"
    ),
)


@pytest.fixture
def scratch_db(tmp_path):
    """A DatasetsDB of this test's own, holding one materialized fixture table.

    §2, for the second store phase 2 adds: the default path is
    ``data/uploads/datasets.duckdb`` in the repo — the maintainer's. Everything
    here happens in ``tmp_path`` and the connection is closed afterwards, so no
    ``upload_ds_*`` table is left behind in a store this suite does not own.
    """
    datasets_db, _ = _SQL_MECHANISM  # type: ignore[misc]
    source = tmp_path / QUARRY_FILE
    source.write_bytes(fixture_bytes(QUARRY_FILE))
    db = datasets_db.DatasetsDB(tmp_path / "scratch.duckdb")
    try:
        db.materialize("ds_scratch0001", QUARRY_FILE, source)
        yield db
    finally:
        db.close()


@needs_dataset_sql
def test_the_dataset_store_is_a_different_database_from_the_warehouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate database, separate connection (§3.3). The retail warehouse is
    in-process DuckDB built from the retail fixture; the dataset store is a file
    under ``data/uploads``. No name belongs to both."""
    datasets_db, _ = _SQL_MECHANISM  # type: ignore[misc]
    # The module fixture points DATASETS_DB_PATH at tmp_path, which is the
    # point of it; the default is what a deployment gets, so ask for that.
    monkeypatch.delenv("DATASETS_DB_PATH", raising=False)
    path = datasets_db.default_db_path()
    # A file of its own under ``data/``, not a schema inside the warehouse.
    # Which subdirectory is the author's choice; that it is git-ignored is not,
    # because the file holds the raw cells of somebody's upload.
    assert path.suffix == ".duckdb", path
    assert "data" in path.parts, path
    from .conftest import REPO_ROOT

    ignored = REPO_ROOT / ".gitignore"
    if ignored.is_file():
        rules = ignored.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        covered = "*.duckdb" in rules or any(
            line.strip() and relative.startswith(line.strip().rstrip("/") + "/")
            for line in rules.splitlines()
            if not line.strip().startswith("#")
        )
        assert covered, (
            f"the uploaded-table database lives at {relative} and .gitignore does not "
            "cover it, so a binary file holding readers' raw table cells can be "
            "committed to a graded public repository"
        )
    assert datasets_db.TABLE_PREFIX == "upload_", (
        "uploaded tables need a prefix no retail table can collide with"
    )
    for table in RETAIL_TABLES:
        assert not table.startswith(datasets_db.TABLE_PREFIX)


@needs_dataset_sql
def test_the_dataset_store_never_reaches_the_warehouse(scratch_db) -> None:
    """The fallback §3.3 forbids most specifically: one connection that can see
    both, so a generated query *could* join them even if today's planner does
    not."""
    for sql in (
        'SELECT * FROM "fact_order_items" LIMIT 1',
        'SELECT * FROM "marts"."fact_order_items" LIMIT 1',
    ):
        with pytest.raises(Exception, match="fact_order_items|allow") as raised:
            scratch_db.run("ds_scratch0001", sql, [])
        assert raised.value


@needs_dataset_sql
@pytest.mark.parametrize(
    "sql",
    [
        # A retail table, alone and joined — both directions of §3.3.
        'SELECT SUM("revenue") FROM "fact_order_items" LIMIT 1',
        'SELECT SUM(a."revenue") FROM "upload_ds_scratch0001" a '
        'JOIN "fact_order_items" b ON a."region" = b."region" LIMIT 1',
        # Another reader's upload: one person's file must not reach another's.
        'SELECT SUM("revenue") FROM "upload_ds_somebody_else" LIMIT 1',
        # Not read-only.
        'DROP TABLE "upload_ds_scratch0001"',
        'UPDATE "upload_ds_scratch0001" SET "revenue" = 0',
        'DELETE FROM "upload_ds_scratch0001"',
        # The bookkeeping table is not the reader's business either.
        'SELECT * FROM "insightgpt_dataset_tables" LIMIT 1',
        # Unbounded: a half-million-row upload scanned without a ceiling.
        'SELECT SUM("revenue") FROM "upload_ds_scratch0001"',
    ],
)
def test_a_query_outside_the_datasets_own_table_is_refused(scratch_db, sql: str) -> None:
    """The contract asks for this test by name: "a generated query touching
    anything outside that dataset's own table is a bug, and should be a test"."""
    datasets_db, _ = _SQL_MECHANISM  # type: ignore[misc]
    from app.engine.guardrails import GuardrailError

    with pytest.raises((GuardrailError, datasets_db.DatasetTableError)):
        scratch_db.run("ds_scratch0001", sql, [])


@needs_dataset_sql
def test_the_datasets_own_table_is_queryable_and_its_arithmetic_is_right(
    scratch_db,
) -> None:
    """The guard is only worth having if the permitted query still works, and
    the arithmetic over the whole table is what this fixture pins."""
    result = scratch_db.run(
        "ds_scratch0001",
        'SELECT SUM("revenue") AS total, COUNT(*) AS n '
        'FROM "upload_ds_scratch0001" LIMIT 1',
        [],
    )
    total, count = result.rows[0]
    assert count == QUARRY_ROWS
    assert float(total) == pytest.approx(QUARRY_TOTAL_REVENUE, abs=0.01), (
        f"the materialized table totals {total} where the file totals "
        f"{QUARRY_TOTAL_REVENUE}: a column was mis-typed or rows were dropped"
    )


@needs_dataset_sql
def test_the_derived_schema_is_reported_and_matches_the_file(scratch_db) -> None:
    """"Column names and types inferred, reported, never guessed silently"."""
    table = scratch_db.schema_of("ds_scratch0001")
    assert table is not None
    assert [c.source_name for c in table.columns] == [
        "order_id",
        "order_date",
        "region",
        "product",
        "units",
        "unit_price",
        "revenue",
    ], [c.source_name for c in table.columns]
    assert table.rows == QUARRY_ROWS
    assert not table.truncated, "a 600-row file was reported as truncated"
    line = table.schema_line()
    for column in ("order_id", "region", "revenue"):
        assert column in line, f"the reported schema line omits {column}: {line}"


@needs_dataset_sql
def test_a_truncated_upload_declines_an_aggregate_rather_than_totalling_part(
    tmp_path,
) -> None:
    """§3.4 at the store, and the case the store was built to handle.

    A file past the row cap is loaded up to the cap. A total over what fit,
    presented as the total, is exactly what §3.4 forbids. Driven by lowering
    the cap rather than by a 500,000-row fixture.
    """
    datasets_db, dataset_query = _SQL_MECHANISM  # type: ignore[misc]
    source = tmp_path / QUARRY_FILE
    source.write_bytes(fixture_bytes(QUARRY_FILE))
    db = datasets_db.DatasetsDB(tmp_path / "truncated.duckdb")
    try:
        table = db.materialize("ds_trunc0001", QUARRY_FILE, source, max_rows=100)
        assert table.truncated, (
            f"a 600-row file under a 100-row cap reports rows={table.rows}, "
            f"source_rows={table.source_rows}, truncated={table.truncated}"
        )
        plan = dataset_query.plan_dataset_query(
            f"What is the total revenue in {QUARRY_FILE}?", table
        )
        assert isinstance(plan, dataset_query.DatasetDecline), (
            "an aggregate was planned over a table holding 100 of 600 rows, so the "
            f"figure would be a sum of a sample presented as a total. plan={plan}"
        )
        assert str(getattr(plan, "reason", "")), "the decline carries no reason"
    finally:
        db.close()
