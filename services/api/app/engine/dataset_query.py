"""Answering a numeric question about an uploaded table with one SQL query.

This is the other half of the uploaded-dataset story. Phase 1 indexed an upload
as text, which answers "what is in this file?" and cannot answer "what is the
total revenue in it?" — retrieval returns a handful of row chunks, and summing
those is a fabricated total over a sample. The honest reply was abstention, and
abstention is not an answer anyone can present.

So a tabular upload gets a real table (``app/warehouse/datasets_db.py``) and a
numeric question about it is answered by a single query over **every** row.
Three things make that safe rather than merely impressive:

**The question has to actually ask arithmetic.** :func:`plan_dataset_query`
returns ``None`` for anything that does not, and the engine then answers it from
the file's indexed text exactly as before. Nothing that used to be a retrieval
answer becomes a number.

**A question it cannot answer over the whole table is declined, not
approximated.** A sum over a column that read as text, an aggregate over a file
too large to load completely, an aggregate whose column the question never named
— each returns a :class:`DatasetDecline` carrying the real reason and the real
column list. The engine turns that into an abstention. This module never
narrows an aggregate to the rows that happened to parse.

**The answer says where the number came from.** :func:`dataset_envelope` names
the dataset in the first clause of the prose, carries the exact SQL, cites the
dataset with its derived schema, and states in a caveat that the figure is from
an upload and not from the governed retail warehouse. A number from somebody's
spreadsheet that reads as though it came from the warehouse is the same failure
as a warehouse number presented as theirs, and the only defence against both is
that the provenance is written on the answer rather than inferred from it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..formatting import indian_group
from ..warehouse.datasets_db import (
    DatasetColumn,
    DatasetsDB,
    DatasetsDBUnavailable,
    DatasetTable,
    DatasetTableError,
    get_datasets_db,
)
from ..warehouse.executor import QueryResult
from .envelope import AnswerEnvelope, Chart, ChartSeries, Citation, Table
from .grounding import (
    FILE_EXTENSIONS,
    GROUP_PREPOSITIONS,
    TIME_WORDS,
    VALUE_COLUMN_HINTS,
    detect_aggregate,
    question_asks_about_rows,
    question_years,
)

log = logging.getLogger("insightgpt.dataset_query")

#: Rows returned for a grouped breakdown. Well under the guardrail ceiling: a
#: reader scans a table, they do not scroll a thousand rows of one.
GROUP_ROW_LIMIT = 50

_AGG_SQL = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}
_AGG_PROSE = {
    "sum": "the total of",
    "avg": "the average of",
    "max": "the highest value of",
    "min": "the lowest value of",
}

#: The source type every uploaded-dataset citation carries. The same string the
#: retrieval path uses for an indexed upload, so a reader cannot tell from the
#: citation whether the number came from a query or the text — and does not need
#: to, because both are the same file and neither is the retail corpus.
DATASET_SOURCE_TYPE = "dataset"

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_GROUP_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in GROUP_PREPOSITIONS)
    + r")\s+([a-z0-9 _%-]{2,40})"
)
_WHICH_RE = re.compile(r"(?:which|what)\s+([a-z][a-z0-9_]{1,30})\b")
# Used to take the filename back out of the question before reading it. A file
# called "q3 sales.csv" otherwise contributes the word "sales", which can match
# a column or a category value — a filter nobody asked for.
_FILE_TOKEN_RE = re.compile(
    r"[\w\-.]{1,120}\.(?:" + "|".join(FILE_EXTENSIONS) + r")\b", re.IGNORECASE
)


# --------------------------------------------------------------------------
# Plans and declines
# --------------------------------------------------------------------------
@dataclass
class DatasetPlan:
    """One validated query over one uploaded table."""

    kind: str                       # "aggregate" | "grouped" | "rowcount"
    sql: str
    aggregate: str                  # sum | avg | max | min | count
    description: str                # "the total of revenue"
    #: What the figure is computed over, in words: "all 5000 rows", or "every
    #: row where region is 'North'". The prose uses it verbatim, so a filtered
    #: figure can never be described as covering the whole file.
    scope: str = ""
    column: DatasetColumn | None = None
    group_by: DatasetColumn | None = None
    filter_column: DatasetColumn | None = None
    filter_value: str | None = None
    params: list = field(default_factory=list)


@dataclass
class DatasetDecline:
    """The question was arithmetic, and answering it honestly is not possible."""

    reason: str
    suggestions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Column matching
# --------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_SPLIT.split(text.lower()) if t]


def _phrase(column: DatasetColumn) -> str:
    return " ".join(_tokens(column.source_name) or _tokens(column.name))


def _score_column(question: str, column: DatasetColumn) -> int:
    """How strongly ``question`` names ``column``.

    Deliberately phrase-first. A single shared token is weak evidence — "order"
    appears in both ``order_date`` and "average order value" — so a full-phrase
    match outranks any number of loose token hits, and a three-letter token is
    worth less than a real word.
    """
    q = question.lower()
    phrase = _phrase(column)
    score = 0
    if phrase and (phrase in q or phrase.replace(" ", "_") in q):
        score += 10
    for token in set(_tokens(phrase)):
        if re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", q):
            score += 3 if len(token) >= 4 else 1
    return score


def match_column(
    question: str, columns: list[DatasetColumn], *, exclude: DatasetColumn | None = None,
    prefer_numeric: bool = False,
) -> DatasetColumn | None:
    """The column the question names, or ``None``.

    ``prefer_numeric`` breaks a tie towards a column that can actually be summed.
    "total revenue by region" names both columns equally strongly, and the one
    being totalled is the numeric one; without this the planner picked ``region``
    and then declined its own question for being a sum over text.
    """
    best: DatasetColumn | None = None
    best_score = 0
    for column in columns:
        if exclude is not None and column.name == exclude.name:
            continue
        score = _score_column(question, column)
        if score and prefer_numeric and column.numeric:
            score += 1
        if score > best_score:
            best, best_score = column, score
    return best


def match_group_column(
    question: str, columns: list[DatasetColumn], *, exclude: DatasetColumn | None = None,
    include_which: bool = False,
) -> DatasetColumn | None:
    """The column a "by ..." / "per ..." phrase asks to break down over.

    ``include_which`` additionally reads the "which region has the highest ..."
    shape, where the grouping column is named by the interrogative rather than
    by a preposition. It is narrow on purpose — one word, and only a
    non-numeric column — because "what revenue is in the file" would otherwise
    read ``revenue`` as the thing to group *by* and then total some other
    column instead, which is a confident wrong number rather than a miss.
    """
    q = question.lower()
    for match in _GROUP_RE.finditer(q):
        phrase = match.group(1).strip()
        candidate = match_column(phrase, columns, exclude=exclude)
        if candidate is not None and _score_column(phrase, candidate) >= 3:
            return candidate
    if include_which:
        for match in _WHICH_RE.finditer(q):
            word = match.group(1).strip()
            candidate = match_column(word, columns, exclude=exclude)
            if (
                candidate is not None
                and not candidate.numeric
                and _score_column(word, candidate) >= 3
            ):
                return candidate
    return None


def _only_numeric_column(question: str, table: DatasetTable) -> DatasetColumn | None:
    """The single numeric column, when the question clearly wants a figure.

    Used only when the question names no column at all and the table leaves no
    choice. It is a reported inference, not a silent one: the answer and the
    caveats both name the column that was summed.
    """
    numeric = table.numeric_columns
    if len(numeric) != 1:
        return None
    q = question.lower()
    if any(re.search(rf"(?<![\w]){re.escape(h)}(?![\w])", q) for h in VALUE_COLUMN_HINTS):
        return numeric[0]
    return None


def _without_filename(question: str, file_name: str) -> str:
    """``question`` with any filename removed, so it cannot be read as data."""
    text = question
    if file_name:
        text = re.sub(re.escape(file_name), " ", text, flags=re.IGNORECASE)
    return _FILE_TOKEN_RE.sub(" ", text)


def _column_list(columns: list[DatasetColumn]) -> str:
    return ", ".join(c.source_name for c in columns) or "none"


# --------------------------------------------------------------------------
# Value filters — "total revenue for North in my_sales.csv"
# --------------------------------------------------------------------------
#: A value shorter than this is not matched. Two-letter codes ("ok", "no") turn
#: up inside ordinary words and sentences, and a spurious filter is a wrong
#: answer rather than a miss.
MIN_VALUE_LENGTH = 3


def match_value_filters(
    question: str, table: DatasetTable
) -> list[tuple[DatasetColumn, str]]:
    """Every (column, value) pair the question names from a category column.

    This is the planner's way of noticing that a question asks about *part* of
    the table. One pair becomes a ``WHERE`` clause; more than one in different
    columns is an ambiguity the planner refuses rather than resolves.
    """
    q = question.lower()
    found: list[tuple[DatasetColumn, str]] = []
    for column in table.columns:
        for value in column.categories:
            if len(value) < MIN_VALUE_LENGTH:
                continue
            token = value.lower()
            # Never read a value that is also a column name as a filter: in
            # "total revenue by category" the word is naming the column.
            if any(token == _phrase(c) for c in table.columns):
                continue
            if re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", q):
                found.append((column, value))
    return found


def _period_words(question: str, table: DatasetTable) -> str | None:
    """A time reference the planner cannot honour, when the table has dates.

    Scoping an uploaded table to a period is not implemented. Summing the whole
    column in answer to "total revenue in 2026" would be the right arithmetic on
    the wrong rows, so the question is declined and says which part is missing.
    """
    if not any(c.type == "DATE" for c in table.columns):
        return None
    q = question.lower()
    years = question_years(q)
    if years:
        return str(years[0])
    for word in TIME_WORDS:
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", q):
            return word
    return None


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
def plan_dataset_query(
    question: str, table: DatasetTable
) -> DatasetPlan | DatasetDecline | None:
    """Decide how (or whether) to answer ``question`` from ``table``.

    * :class:`DatasetPlan` — run this query.
    * :class:`DatasetDecline` — the question is arithmetic and cannot be
      answered over the whole table; the engine abstains with ``reason``.
    * ``None`` — not an arithmetic question; answer it from the indexed text.
    """
    # The filename is not part of the question's vocabulary: it names the file,
    # it does not name a column or a value in it.
    question = _without_filename(question, table.file_name)

    aggregate = detect_aggregate(question)
    if aggregate is None:
        return None

    # The breakdown column is read first, from its own "by ..." / "per ..."
    # phrase, and then excluded from the search for the column being
    # aggregated. The other order ties "total revenue by region" between the
    # two columns and resolves it on declaration order.
    group_by = match_group_column(
        question, table.columns, include_which=aggregate in ("max", "min")
    )
    column = match_column(
        question, table.columns, exclude=group_by,
        prefer_numeric=aggregate in ("sum", "avg"),
    )

    # "how many rows / records are in it" counts the rows, not a column.
    wants_rows = aggregate == "count" and (column is None or question_asks_about_rows(question))

    # An aggregate is a claim about the whole table. If only part of the file
    # was loaded, the whole table is not available and the only honest reply is
    # to say so (honesty rule §3.4) - never a total of the part that fit.
    if table.truncated:
        return DatasetDecline(
            reason=(
                f"'{table.file_name}' holds {table.source_rows} rows and only the "
                f"first {table.rows} could be loaded, so any total, average or "
                "count over it would describe part of the file rather than the "
                "file. I will not report a figure computed from a sample."
            ),
            suggestions=["Split the file into smaller uploads, or ask about a single row."],
        )

    # Scoping an upload to a period is not built. Totalling the whole column in
    # answer to "total revenue in 2026" is the right arithmetic over the wrong
    # rows, which is the one kind of wrong answer that reads as a right one.
    period = _period_words(question, table)
    if period is not None:
        return DatasetDecline(
            reason=(
                f"I cannot yet scope '{table.file_name}' to a period, and '{period}' "
                "asks me to. Totalling the whole column instead would answer a "
                "different question than the one you asked."
            ),
            suggestions=[
                f"Ask for the {aggregate} over the whole file, or broken down "
                f"by one of: {_column_list(table.columns)}"
            ],
        )

    # A value from a category column means the question asks about PART of the
    # table. One value becomes a WHERE clause; anything more ambiguous is
    # refused rather than guessed at.
    filters = match_value_filters(question, table)
    if len({c.name for c, _ in filters}) > 1 or len(filters) > 1:
        named = ", ".join(f"{v} ({c.source_name})" for c, v in filters[:4])
        return DatasetDecline(
            reason=(
                f"that question names more than one value from '{table.file_name}' "
                f"({named}), and I can only narrow to one at a time. Answering over "
                "all of them, or over just the first, would both be wrong."
            ),
            suggestions=[],
        )
    where = filters[0] if filters else None

    if wants_rows:
        return _rowcount_plan(table, group_by, where)

    if column is None:
        column = _only_numeric_column(question, table)
    if column is None:
        if aggregate in ("sum", "avg", "count"):
            return DatasetDecline(
                reason=(
                    f"I could not tell which column of '{table.file_name}' to "
                    f"{'count' if aggregate == 'count' else _AGG_PROSE[aggregate].split()[1]}. "
                    f"Its columns are: {_column_list(table.columns)}."
                ),
                suggestions=[
                    f"Ask for the {aggregate} of {c.source_name} in {table.file_name}"
                    for c in (table.numeric_columns or table.columns)[:3]
                ],
            )
        # "which product is the most ..." without a column is a question about
        # the file's content; retrieval over its text is the right path.
        return None

    if aggregate == "count":
        # A count of non-null values in a named column is a real question, and
        # distinct from COUNT(*). Spell it out in the prose rather than letting
        # the two look alike.
        return _aggregate_plan(table, "count", column, group_by, where)

    if aggregate in ("max", "min") and group_by is not None and column.numeric:
        # "which region has the highest revenue" is a ranking of per-group
        # totals, not a MAX of individual rows. The plan says so in words and
        # the SQL shows it, because reading "highest" as SUM is an
        # interpretation and an interpretation has to be visible.
        return _aggregate_plan(
            table, "sum", column, group_by, where,
            descending=aggregate == "max",
            prose_suffix=", highest first" if aggregate == "max" else ", lowest first",
        )

    if not column.numeric and aggregate in ("sum", "avg"):
        return DatasetDecline(
            reason=(
                f"the column '{column.source_name}' in '{table.file_name}' was read "
                f"as text, not numbers, so a {'total' if aggregate == 'sum' else 'average'} "
                "of it would be invented. "
                f"Numeric columns in this file: {_column_list(table.numeric_columns)}."
            ),
            suggestions=[
                f"Ask for the {aggregate} of {c.source_name} in {table.file_name}"
                for c in table.numeric_columns[:3]
            ],
        )

    if column.type == "VARCHAR" and aggregate in ("max", "min"):
        # Fall through to retrieval rather than decline.
        #
        # "the most common complaint in reviews.csv" parses as an extremum
        # over a text column, and the SQL answer to that would be an
        # alphabetical ordering - not what was asked. But declining was the
        # wrong reply too: the reader asked a thematic question about a file
        # whose text is indexed, which is precisely what retrieval is for.
        # Explaining why SQL cannot do it, to someone who never asked for
        # SQL, reads as a refusal of the question itself.
        #
        # Returning None is safe because it emits no figure: the caller routes
        # to the documents, and the answer arrives cited. The decline remains
        # the right outcome wherever a *number* would otherwise be invented.
        return None

    return _aggregate_plan(table, aggregate, column, group_by, where)


def _alias(aggregate: str, column: DatasetColumn | None) -> str:
    base = f"{aggregate}_{column.name}" if column is not None else "row_count"
    return base[:60]


Filter = tuple[DatasetColumn, str] | None


def _where(table: DatasetTable, where: Filter) -> tuple[str, list, str]:
    """``(sql_clause, params, scope_prose)`` for an optional single filter."""
    if where is None:
        return "", [], f"all {table.rows} rows"
    column, value = where
    return (
        f' WHERE "{column.name}" = ?',
        [value],
        f"every row where {column.source_name} is '{value}'",
    )


def _rowcount_plan(
    table: DatasetTable, group_by: DatasetColumn | None, where: Filter = None
) -> DatasetPlan:
    clause, params, scope = _where(table, where)
    filter_column, filter_value = (where or (None, None))
    if group_by is not None:
        sql = (
            f'SELECT "{group_by.name}" AS "{group_by.name}", COUNT(*) AS row_count '
            f'FROM "{table.table}"{clause} GROUP BY 1 ORDER BY 2 DESC '
            f'LIMIT {GROUP_ROW_LIMIT}'
        )
        return DatasetPlan(
            kind="grouped", sql=sql, aggregate="count",
            description=f"the number of rows by {group_by.source_name}",
            scope=scope, group_by=group_by, params=params,
            filter_column=filter_column, filter_value=filter_value,
        )
    sql = f'SELECT COUNT(*) AS row_count FROM "{table.table}"{clause} LIMIT 1'
    return DatasetPlan(
        kind="rowcount", sql=sql, aggregate="count", description="the number of rows",
        scope=scope, params=params,
        filter_column=filter_column, filter_value=filter_value,
    )


def _aggregate_plan(
    table: DatasetTable, aggregate: str, column: DatasetColumn,
    group_by: DatasetColumn | None, where: Filter = None, *,
    descending: bool | None = None, prose_suffix: str = "",
) -> DatasetPlan:
    function = "COUNT" if aggregate == "count" else _AGG_SQL[aggregate]
    alias = _alias(aggregate, column)
    clause, params, scope = _where(table, where)
    filter_column, filter_value = (where or (None, None))
    prose = (
        f"the number of values in {column.source_name}" if aggregate == "count"
        else f"{_AGG_PROSE[aggregate]} {column.source_name}"
    )
    if group_by is not None:
        if descending is None:
            descending = aggregate != "min"
        sql = (
            f'SELECT "{group_by.name}" AS "{group_by.name}", '
            f'{function}("{column.name}") AS "{alias}" '
            f'FROM "{table.table}"{clause} GROUP BY 1 '
            f'ORDER BY 2 {"DESC" if descending else "ASC"} '
            f"LIMIT {GROUP_ROW_LIMIT}"
        )
        return DatasetPlan(
            kind="grouped", sql=sql, aggregate=aggregate,
            description=f"{prose} by {group_by.source_name}{prose_suffix}",
            scope=scope, column=column, group_by=group_by, params=params,
            filter_column=filter_column, filter_value=filter_value,
        )
    sql = (
        f'SELECT {function}("{column.name}") AS "{alias}" '
        f'FROM "{table.table}"{clause} LIMIT 1'
    )
    return DatasetPlan(
        kind="aggregate", sql=sql, aggregate=aggregate, description=prose,
        scope=scope, column=column, params=params,
        filter_column=filter_column, filter_value=filter_value,
    )


# --------------------------------------------------------------------------
# Rendering the answer
# --------------------------------------------------------------------------
def _render(value: Any) -> str:
    """A number as prose, with no unit attached.

    Deliberately unformatted as currency. The governed catalog knows a column of
    numbers is rupees; an uploaded spreadsheet's column is whatever the uploader
    meant, so stamping ``₹`` on it would be the engine asserting something it
    does not know — and would make the figure read exactly like a warehouse one.
    """
    if value is None:
        return "no value"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return indian_group(value)
    if isinstance(value, float):
        return indian_group(value, 0) if value.is_integer() else indian_group(value, 2)
    return str(value)


def _provenance_caveats(
    table: DatasetTable, plan: DatasetPlan, returned_rows: int = 0
) -> list[str]:
    caveats = [
        f"This figure was computed by SQL over your uploaded file "
        f"'{table.file_name}', not from the governed retail warehouse.",
        f"Derived schema for '{table.file_name}': {table.schema_line()}",
    ]
    if plan.filter_column is not None:
        caveats.insert(
            1,
            f"It covers only the rows where {plan.filter_column.source_name} is "
            f"'{plan.filter_value}', not all {table.rows} rows of the file.",
        )
    # The prose says the breakdown is cut off; the caveat list is the
    # structured channel that says it, so a client rendering caveats rather
    # than reading the sentence sees it too.
    if returned_rows >= GROUP_ROW_LIMIT:
        caveats.append(
            f"Only the top {GROUP_ROW_LIMIT} groups are shown; the file has more "
            f"than that, so this is not the whole breakdown."
        )
    transformed = [c for c in table.columns if c.transform]
    if plan.column is not None and plan.column.transform:
        caveats.append(
            f"Column '{plan.column.source_name}' was {plan.column.transform}."
        )
    elif transformed:
        caveats.append(
            "Some columns were read as numbers after removing thousands separators "
            "and currency symbols: "
            + ", ".join(c.source_name for c in transformed)
        )
    return caveats


def dataset_envelope(
    table: DatasetTable, plan: DatasetPlan, result: QueryResult
) -> AnswerEnvelope:
    """The answer, with its provenance written on it rather than implied."""
    scope = plan.scope or f"all {table.rows} rows"
    lead = (
        f"From your uploaded dataset '{table.file_name}' (not the governed retail "
        f"warehouse), {plan.description} over {scope} is"
    )
    if plan.kind == "grouped":
        top = result.rows[0] if result.rows else None
        if top is None:
            answer = (
                f"Your uploaded dataset '{table.file_name}' has {table.rows} rows, but "
                f"the breakdown of {plan.description} came back empty."
            )
        else:
            # "The full breakdown" was said whether or not it was full. A
            # breakdown by order_id over 600 groups returns the first 50 and
            # read as complete: two true sentences either side framing a
            # false one, so a reader who checked the parts found them right.
            # The row cap is real and fine; describing it as everything is
            # not.
            capped = len(result.rows) >= GROUP_ROW_LIMIT
            closing = (
                f"The top {len(result.rows)} rows and the exact query are below; "
                f"the breakdown is cut off at {GROUP_ROW_LIMIT} groups."
                if capped
                else "The full breakdown and the exact query are below."
            )
            answer = (
                f"From your uploaded dataset '{table.file_name}' (not the governed "
                f"retail warehouse), {plan.description}, over {scope}. "
                f"The table starts with {top[0]} at {_render(top[-1])}. {closing}"
            )
    else:
        value = result.rows[0][0] if result.rows and result.rows[0] else None
        answer = (
            f"{lead} {_render(value)}. "
            f"That is one SQL query over the uploaded table, shown below."
        )

    chart = None
    if plan.kind == "grouped" and len(result.columns) >= 2 and result.rows:
        chart = Chart(
            type="bar", x=result.columns[0],
            series=[ChartSeries(name=plan.description, y=result.columns[-1])],
            data_ref="tables[0]",
        )

    return AnswerEnvelope(
        answer=answer,
        route="structured",
        sql=[plan.sql],
        tables=[
            Table(
                title=f"{table.file_name} — {plan.description}",
                columns=result.columns,
                rows=result.rows,
            )
        ],
        citations=[
            Citation(
                n=1,
                doc_id=table.dataset_id,
                source_type=DATASET_SOURCE_TYPE,
                title=table.file_name,
                snippet=table.schema_line(),
            )
        ],
        chart=chart,
        confidence="high",
        caveats=_provenance_caveats(table, plan, len(result.rows)),
    )


# --------------------------------------------------------------------------
# Resolving a named file to a queryable table
# --------------------------------------------------------------------------
@dataclass
class DatasetMatch:
    """What the engine learned about the file a question named."""

    #: False when the dataset registry could not be consulted at all. The
    #: engine must not claim a file is unknown on the strength of a registry it
    #: could not read.
    registry_available: bool = True
    dataset_id: str | None = None
    file_name: str | None = None
    status: str | None = None
    kind: str | None = None
    table: DatasetTable | None = None
    #: Why there is no table, when there is a dataset but no table. Logged, and
    #: never presented as though the question had been answered.
    note: str | None = None
    known_names: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.dataset_id is not None


def _normalize_name(name: str) -> str:
    stem = Path(str(name).replace("\\", "/")).name.lower()
    return re.sub(r"[\s_\-]+", "", stem)


def resolve_named_file(named_file: str, db: DatasetsDB | None = None) -> DatasetMatch:
    """Find the uploaded dataset a question named, and its table if it has one.

    The registry is the authority on whether the file exists; this module is the
    authority on whether it has a table. Keeping those apart is what lets the
    engine abstain on "a file I do not have" while still sending a *document*
    upload down the retrieval path.
    """
    try:
        from ..api import datasets_store
    except Exception as exc:  # noqa: BLE001 - no registry in this deployment
        log.info("dataset registry unavailable (%s); treating the file as unknown", exc)
        return DatasetMatch(registry_available=False)

    try:
        records = datasets_store.list_all()
    except Exception as exc:  # noqa: BLE001 - a broken registry is not a lie
        log.warning("dataset registry could not be listed: %s", exc)
        return DatasetMatch(registry_available=False)

    known_names = [r.name for r in records]
    wanted = _normalize_name(named_file)
    matches = [r for r in records if _normalize_name(r.name) == wanted]
    if not matches:
        return DatasetMatch(known_names=known_names)

    # Newest first, as the registry already sorts; a re-upload wins.
    record = matches[0]
    match = DatasetMatch(
        dataset_id=record.id,
        file_name=record.name,
        status=record.status,
        kind=record.kind,
        known_names=known_names,
    )
    if record.kind != "tabular":
        match.note = "indexed as a document, so it has no table to query"
        return match
    if record.status != "ready":
        match.note = f"status is {record.status}, so its table is not available yet"
        return match
    if not record.path:
        match.note = "its stored upload could not be located"
        return match

    try:
        match.table = (db or get_datasets_db()).ensure(record.id, record.name, record.path)
    except (DatasetTableError, DatasetsDBUnavailable) as exc:
        match.note = str(exc)
        log.info("no queryable table for dataset %s: %s", record.id, exc)
    except Exception as exc:  # noqa: BLE001 - never fail a question on this path
        match.note = f"the table could not be built: {exc}"
        log.warning("unexpected failure building table for %s: %s", record.id, exc)
    return match


__all__ = [
    "DATASET_SOURCE_TYPE",
    "GROUP_ROW_LIMIT",
    "MIN_VALUE_LENGTH",
    "DatasetDecline",
    "DatasetMatch",
    "DatasetPlan",
    "dataset_envelope",
    "match_column",
    "match_group_column",
    "match_value_filters",
    "plan_dataset_query",
    "resolve_named_file",
]
