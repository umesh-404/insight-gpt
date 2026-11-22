"""The uploaded-datasets database — a tabular upload's own real table.

An uploaded CSV is indexed as text, which answers "what is in this file?" and
cannot answer "what is the total revenue in it?": retrieval returns a handful of
row chunks and summing those would be a fabricated total over a sample. This
module gives a tabular upload a **table**, so that question is answered by a
query over every row or not at all.

Four properties are deliberate, and each one is a rule rather than a preference:

**Its own database.** Uploaded tables live in their own DuckDB file under
``data/ingested/``, with their own connection and their own allow-list. They are
never attached to the retail warehouse, never joined with it, and never a
fallback for it. A figure from somebody's spreadsheet that reads as though it
came from the governed warehouse is the same lie as the reverse, so the two
cannot meet at the connection level, let alone in a query.

**One table per dataset, named from its ``dataset_id``.** The allow-list handed
to :func:`~app.engine.guardrails.validate_sql` on every call contains exactly
one name: that dataset's own table. A generated query that reaches any other
table — another upload, a retail fact table, a DuckDB catalog view — fails
validation rather than returning rows.

**The schema is derived and reported, never silently guessed.** Column types are
inferred by scanning every loaded value, and the inference (including any
cleanup applied to make a column numeric) travels with the table so the answer
can say what it did. A column that did not read as numbers stays text, and an
aggregate over it is declined instead of coerced.

**A partial table cannot answer a whole-table question.** Loading stops at
:data:`MAX_ROWS`; when it does, ``truncated`` is set and the planner refuses
aggregates over that table. See ``app/engine/dataset_query.py``.

One implementation note carried over from ``executor.py``, where it was a real
bug: a DuckDB *connection* holds a single result set and ``execute()`` replaces
it, so two concurrent queries on one shared connection hand each other's rows
back. Every read here goes through ``cursor()``, which owns its own result set.
Writes additionally take a lock, because materializing a table is several
statements that must not interleave with another dataset's.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..engine.guardrails import GuardrailError, validate_sql
from .executor import QueryResult

log = logging.getLogger("insightgpt.datasets_db")

#: Rows loaded per dataset. A file larger than this is loaded up to the cap and
#: marked ``truncated``; the planner then declines aggregates over it rather
#: than reporting a total of the part that fit (honesty rule §3.4).
MAX_ROWS = 500_000

#: Distinct values kept per text column, as the planner's evidence that a
#: question named a value rather than a column. A column with more distinct
#: values than this is not a category column and gets none.
MAX_CATEGORIES = 50

#: Columns per dataset. A wider file is rejected rather than trimmed, because a
#: dropped column is a column whose absence nothing on screen would explain.
MAX_COLUMNS = 256

#: Every generated query carries a LIMIT, and this is the ceiling it may use.
MAX_LIMIT = 1_000

#: Tables are prefixed so an upload's table can never collide with a retail
#: table name even by accident, in any future where the two share a namespace.
TABLE_PREFIX = "upload_"

#: Bookkeeping table: lets the schema of an already-materialized upload survive
#: a restart, so a question does not re-read a 25MB file to learn its columns.
_META_TABLE = "insightgpt_dataset_tables"

_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_UNSAFE_IDENT = re.compile(r"[^a-z0-9_]+")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Characters stripped before deciding a column is numeric. Thousands
#: separators and currency symbols only — never ``%``, which would change 12%
#: into 12 and quietly rescale the column by a hundred.
_NUMERIC_NOISE = re.compile(r"[,₹$€£]")

TABULAR_SUFFIXES = (".csv", ".tsv", ".json", ".xlsx")


class DatasetTableError(RuntimeError):
    """The upload could not be made into a table. Permanent for that file."""


class DatasetsDBUnavailable(RuntimeError):
    """The uploaded-datasets database could not be opened at all."""


# --------------------------------------------------------------------------
# Derived schema
# --------------------------------------------------------------------------
class DatasetColumn(BaseModel):
    """One column of an uploaded table, as it was actually read."""

    name: str          # the SQL identifier (sanitized, unique)
    source_name: str   # the header exactly as the file spelled it
    type: str          # BIGINT | DOUBLE | DATE | VARCHAR
    #: Set when values had to be cleaned to read as numbers. Reported, because
    #: "I summed this column after removing currency symbols" is a different
    #: claim from "I summed this column".
    transform: str | None = None
    #: The distinct values of a low-cardinality text column, up to
    #: :data:`MAX_CATEGORIES`. Their only purpose is to let the planner notice
    #: that a question names a *value* ("total revenue for North") rather than a
    #: column, which is a request to filter. Without them the planner summed the
    #: whole column and labelled it "over all 5,000 rows" — true about what it
    #: computed, and the wrong answer to the question asked.
    categories: list[str] = Field(default_factory=list)

    @property
    def numeric(self) -> bool:
        return self.type in ("BIGINT", "DOUBLE")


class DatasetTable(BaseModel):
    """A materialized upload: which table, which columns, how much of it."""

    dataset_id: str
    file_name: str
    table: str
    columns: list[DatasetColumn] = Field(default_factory=list)
    rows: int = 0
    source_rows: int = 0

    @property
    def truncated(self) -> bool:
        return self.source_rows > self.rows

    @property
    def numeric_columns(self) -> list[DatasetColumn]:
        return [c for c in self.columns if c.numeric]

    def column(self, name: str) -> DatasetColumn | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def schema_line(self) -> str:
        """A one-line description of the derived schema, for the answer."""
        parts = []
        for c in self.columns:
            text = f"{c.source_name} ({c.type.lower()}"
            text += f", {c.transform})" if c.transform else ")"
            parts.append(text)
        return f"{self.rows} rows; columns: " + ", ".join(parts)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------
def table_name_for(dataset_id: str) -> str:
    """The one table name a dataset is allowed to own.

    Rejects any id that is not a bare identifier instead of escaping it: the
    name is interpolated into DDL, and an id that needs quoting is an id we did
    not generate.
    """
    if not dataset_id or not _SAFE_IDENT.match(dataset_id):
        raise DatasetTableError(f"unsafe dataset id for a table name: {dataset_id!r}")
    return f"{TABLE_PREFIX}{dataset_id}"


def safe_column_name(raw: str, index: int, used: set[str]) -> str:
    """A unique SQL identifier for a column header."""
    cleaned = _UNSAFE_IDENT.sub("_", str(raw or "").strip().lower()).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"col_{index + 1}" if not cleaned else f"c_{cleaned}"
    cleaned = cleaned[:60]
    candidate = cleaned
    suffix = 2
    while candidate in used:
        candidate = f"{cleaned}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


# --------------------------------------------------------------------------
# Reading the uploaded file into rows of text
# --------------------------------------------------------------------------
def read_table_file(path: str | Path, *, max_rows: int = MAX_ROWS) -> tuple[
    list[str], list[list[Any]], int
]:
    """``(headers, rows, source_rows)`` from a tabular upload.

    ``source_rows`` is how many data rows the file actually held, which is how
    the caller learns that ``rows`` is only part of it.
    """
    p = Path(path)
    if not p.is_file():
        raise DatasetTableError(f"the stored upload is missing: {p.name}")
    suffix = p.suffix.lower()
    if suffix in (".csv", ".tsv"):
        return _read_delimited(p, "\t" if suffix == ".tsv" else ",", max_rows)
    if suffix == ".json":
        return _read_json(p, max_rows)
    if suffix == ".xlsx":
        return _read_xlsx(p, max_rows)
    raise DatasetTableError(f"{suffix or 'this file type'} is not a table InsightGPT can query")


def _read_delimited(path: Path, delimiter: str, max_rows: int) -> tuple[
    list[str], list[list[Any]], int
]:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    headers: list[str] | None = None
    rows: list[list[Any]] = []
    source_rows = 0
    for raw in reader:
        if not any(str(cell).strip() for cell in raw):
            continue
        if headers is None:
            headers = [str(c).strip() for c in raw]
            continue
        source_rows += 1
        if len(rows) < max_rows:
            rows.append(list(raw))
    if not headers:
        raise DatasetTableError("the file has no header row, so its columns cannot be named")
    return headers, rows, source_rows


def _read_json(path: Path, max_rows: int) -> tuple[list[str], list[list[Any]], int]:
    try:
        parsed = json.loads(path.read_bytes().decode("utf-8-sig", errors="replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise DatasetTableError(f"the JSON could not be read: {exc}") from exc
    if not isinstance(parsed, list) or not parsed or not all(
        isinstance(item, dict) for item in parsed
    ):
        raise DatasetTableError(
            "this JSON is not an array of records, so it has no rows and columns"
        )
    headers: list[str] = []
    for item in parsed:
        for key in item:
            if key not in headers:
                headers.append(str(key))
    rows = [[item.get(k, None) for k in headers] for item in parsed[:max_rows]]
    return headers, rows, len(parsed)


def _read_xlsx(path: Path, max_rows: int) -> tuple[list[str], list[list[Any]], int]:
    try:
        import openpyxl  # a core dependency; guarded so a slim install degrades
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment shape
        raise DatasetTableError(
            "spreadsheet support is not installed in this service, so this "
            "workbook cannot be queried"
        ) from exc
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any openpyxl failure is a bad file
        raise DatasetTableError(f"the workbook could not be read: {exc}") from exc
    try:
        sheet = book[book.sheetnames[0]]
        headers: list[str] | None = None
        rows: list[list[Any]] = []
        source_rows = 0
        for raw in sheet.iter_rows(values_only=True):
            if raw is None or not any(c is not None and str(c).strip() for c in raw):
                continue
            if headers is None:
                headers = [str(c).strip() if c is not None else "" for c in raw]
                continue
            source_rows += 1
            if len(rows) < max_rows:
                rows.append(list(raw))
    finally:
        book.close()
    if not headers:
        raise DatasetTableError("the sheet has no header row, so its columns cannot be named")
    return headers, rows, source_rows


# --------------------------------------------------------------------------
# Type inference
# --------------------------------------------------------------------------
def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_int(text: str) -> bool:
    body = text[1:] if text[:1] in "+-" else text
    return body.isdigit()


def _is_float(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def infer_column(values: list[Any]) -> tuple[str, str | None]:
    """``(duckdb_type, transform)`` for one column's values.

    The scan is over every loaded value, and one value that does not fit demotes
    the column: a "revenue" column with a ``N/A`` in it is text, and a sum over
    text is declined rather than computed over the rows that happened to parse.
    """
    seen = [_as_text(v) for v in values]
    present = [t for t in seen if t]
    if not present:
        return "VARCHAR", None

    if all(_is_int(t) for t in present):
        return "BIGINT", None
    if all(_is_float(t) for t in present):
        return "DOUBLE", None
    if all(_ISO_DATE.match(t) for t in present):
        return "DATE", None

    # Numbers wearing presentation: "1,250" / "₹1,250.50". The cleanup is
    # narrow and is reported on the column, never applied silently.
    stripped = [_NUMERIC_NOISE.sub("", t) for t in present]
    if all(s and _is_int(s) for s in stripped):
        return "BIGINT", "read as a number after removing thousands separators and currency symbols"
    if all(s and _is_float(s) for s in stripped):
        return "DOUBLE", "read as a number after removing thousands separators and currency symbols"
    return "VARCHAR", None


def _convert(value: Any, column: DatasetColumn) -> Any:
    text = _as_text(value)
    if not text:
        return None
    if column.type == "VARCHAR":
        return text
    if column.transform:
        text = _NUMERIC_NOISE.sub("", text)
    try:
        if column.type == "BIGINT":
            return int(text)
        if column.type == "DOUBLE":
            return float(text)
        if column.type == "DATE":
            return date.fromisoformat(text)
    except ValueError:  # pragma: no cover - inference scanned every value
        return None
    return text


def _categories(values: list[Any]) -> list[str]:
    """The distinct values of a text column, when there are few enough of them.

    A column with more than :data:`MAX_CATEGORIES` distinct values is a free-text
    column, not a category, and gets an empty list rather than a truncated one —
    a partial value set would let the planner conclude a question names no value
    when it does.
    """
    distinct: set[str] = set()
    for value in values:
        text = _as_text(value)
        if not text:
            continue
        distinct.add(text)
        if len(distinct) > MAX_CATEGORIES:
            return []
    return sorted(distinct)


def _redact_rows(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """Run every cell through the shared redactor before it is stored.

    The same redactor the document path uses, so the two stores cannot
    disagree about what a secret is - a second implementation here would be a
    second thing to keep correct, and the one that drifted would be the one
    nobody was watching.

    Cheap guard first: the redactor is a stack of regexes, and a spreadsheet
    is overwhelmingly short numeric cells that can hold none of the shapes it
    looks for. Skipping those keeps a 500k-row load from paying for the whole
    pattern set per cell.

    The guard is deliberately narrow, because the obvious version of it is
    wrong. A plain length cutoff at 8 let `a@b.com` through - seven
    characters, and unambiguously an email address. So anything holding an
    `@` is always examined, and the length floor is set below the shortest
    address that can exist rather than below the shortest API key.
    """
    from ..api.datasets_store import redact

    redacted_cells = 0
    out: list[list[str]] = []
    for row in rows:
        new_row: list[str] = []
        for cell in row:
            if not cell or (len(cell) < 6 and "@" not in cell):
                new_row.append(cell)
                continue
            cleaned, count = redact(cell)
            if count:
                redacted_cells += 1
            new_row.append(cleaned)
        out.append(new_row)
    return out, redacted_cells


def derive_schema(
    dataset_id: str, file_name: str, headers: list[str], rows: list[list[Any]], source_rows: int
) -> DatasetTable:
    """The derived schema for a read file, with no database involved."""
    if not headers:
        raise DatasetTableError("the file has no columns")
    if len(headers) > MAX_COLUMNS:
        raise DatasetTableError(
            f"this file has {len(headers)} columns, more than the {MAX_COLUMNS} "
            "InsightGPT will load"
        )
    used: set[str] = set()
    columns: list[DatasetColumn] = []
    for i, header in enumerate(headers):
        values = [r[i] if i < len(r) else None for r in rows]
        duck_type, transform = infer_column(values)
        columns.append(
            DatasetColumn(
                name=safe_column_name(header, i, used),
                source_name=str(header).strip() or f"column {i + 1}",
                type=duck_type,
                transform=transform,
                categories=_categories(values) if duck_type == "VARCHAR" else [],
            )
        )
    return DatasetTable(
        dataset_id=dataset_id,
        file_name=file_name,
        table=table_name_for(dataset_id),
        columns=columns,
        rows=len(rows),
        source_rows=source_rows,
    )


# --------------------------------------------------------------------------
# The database
# --------------------------------------------------------------------------
def default_db_path() -> Path:
    """Where the uploaded-datasets database lives.

    ``data/ingested/`` — beside the raw uploads it is built from, and already
    git-ignored, so a binary database cannot reach a commit by being in a
    directory nobody thought to exclude.
    """
    override = os.getenv("DATASETS_DB_PATH")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    root = next((p for p in here.parents if (p / "services").is_dir()), here.parents[4])
    return root / "data" / "ingested" / "datasets.duckdb"


class DatasetsDB:
    """Uploaded tables, in their own DuckDB database.

    Never holds a handle on the retail warehouse, and nothing here can reach it:
    the allow-list on every :meth:`run` is a single table name.
    """

    def __init__(self, path: str | Path | None = None):
        self._path = ":memory:" if path == ":memory:" else str(path or default_db_path())
        self._lock = threading.Lock()
        self._con: Any = None
        self._tables: dict[str, DatasetTable] = {}

    # ---- connection ------------------------------------------------------
    def _connect(self) -> Any:
        if self._con is not None:
            return self._con
        import duckdb  # local import, as the fixture warehouse does

        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        try:
            # ``enable_external_access=False`` closes DuckDB's own file and HTTP
            # readers (read_csv, read_parquet, httpfs) from inside a query. We
            # load rows through parameters, so nothing legitimate needs them,
            # and the guardrails' deny-list is then not the only thing standing
            # between a generated query and the filesystem.
            self._con = duckdb.connect(
                database=self._path, config={"enable_external_access": False}
            )
        except Exception:  # noqa: BLE001 - an older build may not know the option
            try:
                self._con = duckdb.connect(database=self._path)
            except Exception as exc:  # noqa: BLE001
                raise DatasetsDBUnavailable(
                    f"the uploaded-datasets database could not be opened: {exc}"
                ) from exc
        self._ensure_meta()
        self._load_meta()
        return self._con

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None

    # ---- bookkeeping -----------------------------------------------------
    def _ensure_meta(self) -> None:
        cur = self._con.cursor()
        try:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {_META_TABLE} ("
                "dataset_id VARCHAR PRIMARY KEY, file_name VARCHAR, table_name VARCHAR, "
                "schema_json VARCHAR)"
            )
        finally:
            cur.close()

    def _load_meta(self) -> None:
        cur = self._con.cursor()
        try:
            cur.execute(f"SELECT dataset_id, schema_json FROM {_META_TABLE}")
            rows = cur.fetchall()
        finally:
            cur.close()
        for dataset_id, schema_json in rows:
            try:
                table = DatasetTable.model_validate_json(schema_json)
            except Exception as exc:  # noqa: BLE001 - a stale row is not fatal
                log.warning("ignoring unreadable dataset schema for %s: %s", dataset_id, exc)
                continue
            if self._table_exists(table.table):
                self._tables[dataset_id] = table

    def _table_exists(self, table: str) -> bool:
        cur = self._con.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
            )
            return cur.fetchone() is not None
        finally:
            cur.close()

    # ---- schema reads ----------------------------------------------------
    def schema_of(self, dataset_id: str) -> DatasetTable | None:
        """The materialized schema for ``dataset_id``, or ``None``."""
        with self._lock:
            self._connect()
            return self._tables.get(dataset_id)

    def known_datasets(self) -> list[str]:
        with self._lock:
            self._connect()
            return sorted(self._tables)

    # ---- materialization -------------------------------------------------
    def materialize(
        self, dataset_id: str, file_name: str, path: str | Path,
        *, max_rows: int | None = None,
    ) -> DatasetTable:
        """Load a tabular upload into its own table. Idempotent: a re-load
        replaces that dataset's table and nothing else."""
        headers, rows, source_rows = read_table_file(path, max_rows=max_rows or MAX_ROWS)
        if source_rows == 0:
            raise DatasetTableError("the file has a header but no data rows")
        # Redact BEFORE the schema is derived, let alone written.
        #
        # This store was reading the raw persisted upload. The document path
        # redacts on the way to the vector store, so a key in a cell never
        # reached Qdrant - and then the same file was loaded verbatim into
        # DuckDB, where a grouped query handed it straight back: "The table
        # starts with sk_live_… at 2,901". The dataset row said
        # `redactions: 10` the whole time, describing work this store undid.
        #
        # Before `derive_schema`, not after, so a column of redacted values is
        # typed on what is actually stored rather than on what was discarded.
        rows, cell_redactions = _redact_rows(rows)
        if cell_redactions:
            log.info(
                "dataset %s: redacted %d cell(s) before loading the table",
                dataset_id, cell_redactions,
            )
        table = derive_schema(dataset_id, file_name, headers, rows, source_rows)
        if table.truncated:
            log.warning(
                "dataset %s has %d rows; loaded the first %d, so aggregates over it "
                "will be declined", dataset_id, source_rows, table.rows,
            )
        with self._lock:
            self._connect()
            self._write_table(table, rows)
            self._tables[dataset_id] = table
        return table

    def ensure(self, dataset_id: str, file_name: str, path: str | Path) -> DatasetTable:
        """The dataset's table, materializing it on first use."""
        existing = self.schema_of(dataset_id)
        if existing is not None:
            return existing
        return self.materialize(dataset_id, file_name, path)

    def _write_table(self, table: DatasetTable, rows: list[list[Any]]) -> None:
        """CREATE + INSERT under the write lock (several statements, one table)."""
        columns_ddl = ", ".join(f'"{c.name}" {c.type}' for c in table.columns)
        placeholders = ", ".join("?" for _ in table.columns)
        converted = [
            [_convert(r[i] if i < len(r) else None, c) for i, c in enumerate(table.columns)]
            for r in rows
        ]
        cur = self._con.cursor()
        try:
            # Dropping and recreating *this upload's own* table is the whole
            # point of a re-index; nothing else in this database is touched.
            cur.execute(f'DROP TABLE IF EXISTS "{table.table}"')
            cur.execute(f'CREATE TABLE "{table.table}" ({columns_ddl})')
            if converted:
                cur.executemany(
                    f'INSERT INTO "{table.table}" VALUES ({placeholders})', converted
                )
            cur.execute(f"DELETE FROM {_META_TABLE} WHERE dataset_id = ?", [table.dataset_id])
            cur.execute(
                f"INSERT INTO {_META_TABLE} VALUES (?, ?, ?, ?)",
                [table.dataset_id, table.file_name, table.table, table.model_dump_json()],
            )
        finally:
            cur.close()

    def drop(self, dataset_id: str) -> bool:
        """Forget one upload's table. True when something was removed."""
        with self._lock:
            self._connect()
            table = self._tables.pop(dataset_id, None)
            name = table.table if table else None
            if name is None:
                try:
                    name = table_name_for(dataset_id)
                except DatasetTableError:
                    return False
            cur = self._con.cursor()
            try:
                cur.execute(f'DROP TABLE IF EXISTS "{name}"')
                cur.execute(f"DELETE FROM {_META_TABLE} WHERE dataset_id = ?", [dataset_id])
            finally:
                cur.close()
            return table is not None

    # ---- query -----------------------------------------------------------
    def run(self, dataset_id: str, sql: str, params: list | None = None) -> QueryResult:
        """Run one read-only query against exactly one dataset's table.

        The allow-list is that dataset's table and nothing else, so a query that
        names another upload, a retail table or a catalog view is rejected here
        rather than answered. Every query must also carry a bounded ``LIMIT``.
        """
        table = self.schema_of(dataset_id)
        if table is None:
            raise DatasetTableError(f"dataset {dataset_id} has no queryable table")
        ast = validate_sql(sql, {table.table}, dialect="duckdb")
        _assert_limited(ast)
        with self._lock:
            con = self._connect()
        # One cursor per call: a DuckDB connection holds a single result set and
        # a concurrent ``execute()`` would hand this caller the other query's
        # rows. See the same note in ``executor.py`` — it was a real bug.
        cur = con.cursor()
        try:
            cur.execute(sql, params or [])
            columns = [d[0] for d in cur.description]
            result_rows = [list(r) for r in cur.fetchall()]
        finally:
            cur.close()
        return QueryResult(columns=columns, rows=result_rows)


def _assert_limited(ast: Any) -> None:
    """Reject a query without a bounded LIMIT.

    The planner always emits one; this is the check that keeps that true, so an
    unbounded scan of somebody's half-million-row upload cannot be issued by a
    future caller that forgot.
    """
    limit = ast.args.get("limit") if hasattr(ast, "args") else None
    if limit is None:
        raise GuardrailError("a query over an uploaded table must carry a LIMIT")
    try:
        value = int(limit.expression.this)
    except (AttributeError, TypeError, ValueError) as exc:
        raise GuardrailError("the LIMIT on an uploaded-table query must be a literal") from exc
    if value < 1 or value > MAX_LIMIT:
        raise GuardrailError(f"LIMIT must be between 1 and {MAX_LIMIT}, got {value}")


# --------------------------------------------------------------------------
# Process-wide instance
# --------------------------------------------------------------------------
_DB: DatasetsDB | None = None
_DB_LOCK = threading.Lock()


def get_datasets_db() -> DatasetsDB:
    """The process's uploaded-datasets database."""
    global _DB
    with _DB_LOCK:
        if _DB is None:
            _DB = DatasetsDB()
        return _DB


def set_datasets_db(db: DatasetsDB | None) -> None:
    """Replace the process instance (tests)."""
    global _DB
    with _DB_LOCK:
        _DB = db


__all__ = [
    "MAX_COLUMNS",
    "MAX_LIMIT",
    "MAX_ROWS",
    "TABULAR_SUFFIXES",
    "DatasetColumn",
    "DatasetTable",
    "DatasetTableError",
    "DatasetsDB",
    "DatasetsDBUnavailable",
    "default_db_path",
    "derive_schema",
    "get_datasets_db",
    "infer_column",
    "read_table_file",
    "safe_column_name",
    "set_datasets_db",
    "table_name_for",
]
