"""The uploaded-datasets database: isolation, derived schema, and honesty.

These tests are about the properties that make querying somebody's spreadsheet
safe rather than merely possible:

* an upload's query can reach exactly one table — its own — and not another
  upload, not a retail fact table, not a catalog view;
* every query is read-only and bounded;
* the schema is *derived* from the file and reported, and a column that did not
  read as numbers stays text;
* two datasets queried at once do not hand each other their rows (the bug
  ``executor.py`` documents, which this module must not reintroduce);
* a file too large to load completely is marked as such, so the planner can
  refuse to total part of it.
"""

from __future__ import annotations

import contextlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "fake")

import pytest  # noqa: E402

from app.engine.guardrails import GuardrailError  # noqa: E402
from app.warehouse.datasets_db import (  # noqa: E402
    DatasetsDB,
    DatasetTableError,
    derive_schema,
    infer_column,
    read_table_file,
    safe_column_name,
    table_name_for,
)

SALES = "order_id,region,revenue,units\n1,North,100,2\n2,South,250,1\n3,North,50,4\n"


def _csv(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _db() -> DatasetsDB:
    return DatasetsDB(":memory:")


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------
def test_table_name_is_derived_from_the_dataset_id() -> None:
    assert table_name_for("ds_a1b2c3") == "upload_ds_a1b2c3"


@pytest.mark.parametrize(
    "bad",
    ['ds"; DROP TABLE x; --', "ds a", "ds-1", "", "fact_order_items; --"],
)
def test_an_id_that_would_need_quoting_is_refused(bad: str) -> None:
    """The name is interpolated into DDL, so an id we did not generate is a
    refusal rather than an escaping problem."""
    with pytest.raises(DatasetTableError):
        table_name_for(bad)


def test_column_names_are_sanitized_and_unique() -> None:
    used: set[str] = set()
    assert safe_column_name("Total Revenue (INR)", 0, used) == "total_revenue_inr"
    assert safe_column_name("Total Revenue (INR)", 1, used) == "total_revenue_inr_2"
    assert safe_column_name("", 2, used) == "col_3"
    assert safe_column_name("2026", 3, used) == "c_2026"


# --------------------------------------------------------------------------
# Derived schema
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "2", "3"], "BIGINT"),
        (["1.5", "2", ""], "DOUBLE"),
        (["2026-01-01", "2026-02-02"], "DATE"),
        (["North", "South"], "VARCHAR"),
        # One unparseable value demotes the whole column: a sum over the rows
        # that happened to parse is a sum of a sample.
        (["1", "2", "N/A"], "VARCHAR"),
        ([], "VARCHAR"),
        (["", ""], "VARCHAR"),
    ],
)
def test_type_inference(values: list[str], expected: str) -> None:
    assert infer_column(values)[0] == expected


def test_presentation_numbers_are_converted_and_the_conversion_is_reported() -> None:
    duck_type, transform = infer_column(["₹1,250", "₹980.50"])
    assert duck_type == "DOUBLE"
    assert transform and "currency" in transform


def test_percent_is_not_silently_rescaled() -> None:
    """Stripping ``%`` would turn 12% into 12 and multiply the column by 100."""
    assert infer_column(["12%", "8%"])[0] == "VARCHAR"


def test_schema_line_names_every_column_and_its_type(tmp_path: Path) -> None:
    headers, rows, source_rows = read_table_file(_csv(tmp_path, "s.csv", SALES))
    table = derive_schema("ds_1", "s.csv", headers, rows, source_rows)
    line = table.schema_line()
    assert "3 rows" in line
    for column in ("order_id", "region", "revenue", "units"):
        assert column in line
    assert [c.type for c in table.columns] == ["BIGINT", "VARCHAR", "BIGINT", "BIGINT"]


# --------------------------------------------------------------------------
# Reading files
# --------------------------------------------------------------------------
def test_tsv_json_and_unsupported(tmp_path: Path) -> None:
    tsv = _csv(tmp_path, "t.tsv", "a\tb\n1\t2\n")
    assert read_table_file(tsv)[0] == ["a", "b"]

    js = _csv(tmp_path, "j.json", '[{"a": 1, "b": "x"}, {"a": 2}]')
    headers, rows, source = read_table_file(js)
    assert headers == ["a", "b"] and source == 2 and rows[1] == [2, None]

    with pytest.raises(DatasetTableError):
        read_table_file(_csv(tmp_path, "n.pdf", "not a table"))


def test_json_that_is_not_records_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DatasetTableError):
        read_table_file(_csv(tmp_path, "j.json", '{"a": 1}'))


def test_a_header_with_no_rows_is_not_a_table(tmp_path: Path) -> None:
    db = _db()
    with pytest.raises(DatasetTableError):
        db.materialize("ds_empty", "e.csv", _csv(tmp_path, "e.csv", "a,b\n"))


def test_a_missing_stored_upload_is_a_clear_failure(tmp_path: Path) -> None:
    with pytest.raises(DatasetTableError):
        read_table_file(tmp_path / "gone.csv")


# --------------------------------------------------------------------------
# Materialize + query
# --------------------------------------------------------------------------
def test_materialize_then_query_the_whole_table(tmp_path: Path) -> None:
    db = _db()
    table = db.materialize("ds_1", "s.csv", _csv(tmp_path, "s.csv", SALES))
    assert table.rows == 3 and not table.truncated
    result = db.run("ds_1", f'SELECT SUM("revenue") AS t FROM "{table.table}" LIMIT 1')
    assert result.rows == [[400]]


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    db = _db()
    path = _csv(tmp_path, "s.csv", SALES)
    db.materialize("ds_1", "s.csv", path)
    table = db.materialize("ds_1", "s.csv", path)
    assert db.run("ds_1", f'SELECT COUNT(*) AS c FROM "{table.table}" LIMIT 1').rows == [[3]]


def test_the_schema_survives_a_reconnection(tmp_path: Path) -> None:
    """A restart must not mean re-reading a 25MB file to learn its columns."""
    store = tmp_path / "datasets.duckdb"
    first = DatasetsDB(store)
    first.materialize("ds_1", "s.csv", _csv(tmp_path, "s.csv", SALES))
    first.close()

    second = DatasetsDB(store)
    table = second.schema_of("ds_1")
    assert table is not None and table.rows == 3
    assert second.run("ds_1", f'SELECT SUM("units") AS u FROM "{table.table}" LIMIT 1').rows == [
        [7]
    ]
    second.close()


def test_drop_forgets_one_dataset_and_leaves_the_others(tmp_path: Path) -> None:
    db = _db()
    db.materialize("ds_1", "a.csv", _csv(tmp_path, "a.csv", SALES))
    db.materialize("ds_2", "b.csv", _csv(tmp_path, "b.csv", SALES))
    assert db.drop("ds_1") is True
    assert db.schema_of("ds_1") is None
    assert db.schema_of("ds_2") is not None


# --------------------------------------------------------------------------
# Isolation — the whole point
# --------------------------------------------------------------------------
def test_a_query_cannot_reach_another_upload(tmp_path: Path) -> None:
    db = _db()
    db.materialize("ds_1", "a.csv", _csv(tmp_path, "a.csv", SALES))
    other = db.materialize("ds_2", "b.csv", _csv(tmp_path, "b.csv", SALES))
    with pytest.raises(GuardrailError):
        db.run("ds_1", f'SELECT COUNT(*) AS c FROM "{other.table}" LIMIT 1')


def test_a_query_cannot_reach_the_retail_warehouse(tmp_path: Path) -> None:
    """The retail tables are in a different database entirely; the allow-list
    rejects the name before the question of reachability even arises."""
    db = _db()
    table = db.materialize("ds_1", "a.csv", _csv(tmp_path, "a.csv", SALES))
    for sql in (
        'SELECT SUM(gross_revenue) AS r FROM fact_order_items LIMIT 1',
        f'SELECT COUNT(*) AS c FROM "{table.table}", fact_order_items LIMIT 1',
        f'SELECT (SELECT COUNT(*) FROM dim_date) AS c FROM "{table.table}" LIMIT 1',
    ):
        with pytest.raises(GuardrailError):
            db.run("ds_1", sql)


def test_writes_and_filesystem_reads_are_rejected(tmp_path: Path) -> None:
    db = _db()
    table = db.materialize("ds_1", "a.csv", _csv(tmp_path, "a.csv", SALES))
    for sql in (
        f'DELETE FROM "{table.table}"',
        f'DROP TABLE "{table.table}"',
        f'UPDATE "{table.table}" SET revenue = 0',
        f"SELECT * FROM read_csv_auto('{tmp_path.as_posix()}/a.csv') LIMIT 1",
        f'SELECT * INTO leaked FROM "{table.table}"',
    ):
        with pytest.raises(GuardrailError):
            db.run("ds_1", sql)


def test_every_query_must_carry_a_bounded_limit(tmp_path: Path) -> None:
    db = _db()
    table = db.materialize("ds_1", "a.csv", _csv(tmp_path, "a.csv", SALES))
    with pytest.raises(GuardrailError):
        db.run("ds_1", f'SELECT * FROM "{table.table}"')
    with pytest.raises(GuardrailError):
        db.run("ds_1", f'SELECT * FROM "{table.table}" LIMIT 100000')


def test_a_dataset_without_a_table_cannot_be_queried() -> None:
    db = _db()
    with pytest.raises(DatasetTableError):
        db.run("ds_missing", 'SELECT 1 AS a FROM "upload_ds_missing" LIMIT 1')


# --------------------------------------------------------------------------
# Category values — the planner's evidence that a question names a value
# --------------------------------------------------------------------------
def test_a_free_text_column_gets_no_category_values(tmp_path: Path) -> None:
    """A truncated value set would let the planner conclude a question names no
    value when it does, so a high-cardinality column gets none at all."""
    body = "note\n" + "".join(f"comment number {i}\n" for i in range(80))
    headers, rows, source = read_table_file(_csv(tmp_path, "notes.csv", body))
    table = derive_schema("ds_1", "notes.csv", headers, rows, source)
    assert table.columns[0].categories == []


# --------------------------------------------------------------------------
# Truncation — a partial table cannot answer a whole-table question
# --------------------------------------------------------------------------
def test_a_file_larger_than_the_cap_is_marked_truncated(tmp_path: Path) -> None:
    body = "n\n" + "".join(f"{i}\n" for i in range(50))
    headers, rows, source_rows = read_table_file(_csv(tmp_path, "big.csv", body), max_rows=10)
    table = derive_schema("ds_1", "big.csv", headers, rows, source_rows)
    assert table.rows == 10 and table.source_rows == 50 and table.truncated is True


# --------------------------------------------------------------------------
# Concurrency — the bug executor.py documents
# --------------------------------------------------------------------------
def test_concurrent_queries_each_get_their_own_rows(tmp_path: Path) -> None:
    """A DuckDB connection holds one result set, so a shared one lets two
    callers read each other's rows. Each query here is distinguishable by its
    own column name and value."""
    db = _db()
    bodies = {
        f"ds_{i}": f"c_{i}\n{i * 100}\n" for i in range(1, 7)
    }
    tables = {
        ds: db.materialize(ds, f"{ds}.csv", _csv(tmp_path, f"{ds}.csv", body))
        for ds, body in bodies.items()
    }

    def run(ds: str) -> tuple[str, list[str], list[list]]:
        table = tables[ds]
        name = table.columns[0].name
        result = db.run("".join(ds), f'SELECT "{name}" AS "{name}" FROM "{table.table}" LIMIT 5')
        return ds, result.columns, result.rows

    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(run, list(bodies) * 6))

    for ds, columns, rows in got:
        expected_column = tables[ds].columns[0].name
        assert columns == [expected_column], f"{ds} received another query's columns"
        assert rows == [[int(ds.split('_')[1]) * 100]], f"{ds} received another query's rows"


# --- the second store is on the same side of the redaction boundary ---------
def test_an_uploaded_table_never_stores_a_secret_from_a_cell(tmp_path: Path) -> None:
    """The document path redacted; this one did not, and it serves rows back.

    A key in a *cell* never reached Qdrant, because the indexing path redacts.
    The same file was then loaded verbatim into DuckDB, where a grouped query
    handed it straight back - while the dataset row reported `redactions: 10`,
    describing work this store had undone. Two stores, one boundary, and only
    one of them was honouring it.
    """
    csv = tmp_path / "leaky.csv"
    csv.write_text(
        "order_id,api_key,email,phone,revenue\n"
        "1,sk_live_FAKEFAKEFAKEFAKEFAKE,a@b.com,+91 98765 43210,2901\n"
        "2,sk-live-ABCD1234efgh5678abcdef,ops@example.com,+1 415 555 2671,1899\n",
        encoding="utf-8",
    )
    db = DatasetsDB(tmp_path / "leaky.duckdb")
    try:
        table = db.materialize("ds_leakcheck", "leaky.csv", csv)
        rows = str(db.run("ds_leakcheck", f'SELECT * FROM "{table.table}" LIMIT 10', []).rows)
        for secret in (
            "sk_live_FAKEFAKEFAKEFAKEFAKE",
            "sk-live-ABCD1234efgh5678abcdef",
            "a@b.com",                       # 7 chars: under a naive length guard
            "ops@example.com",
            "98765 43210",
        ):
            assert secret not in rows, f"{secret!r} was stored and served back"
        # The other half of the job: the figures still have to be there.
        assert "2901" in rows and "1899" in rows

        # And the bytes on disk, not just the query result.
        db.close()
        blob = (tmp_path / "leaky.duckdb").read_bytes()
        assert b"sk_live_FAKEFAKEFAKEFAKEFAKE" not in blob
        assert b"ops@example.com" not in blob
    finally:
        with contextlib.suppress(Exception):
            db.close()
