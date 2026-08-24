"""Run tracking — the ``insight.pipeline_runs`` writer (``docs/03`` §5.3).

Every job execution opens a run (``queued`` -> ``running``) and closes it
(``success`` / ``failed``) with a row count and, on failure, the captured error.
The store is backed by Postgres via psycopg and creates the table defensively if
bootstrap has not; with ``POSTGRES_DSN`` unset (or psycopg missing) it degrades
to an in-memory store — logged once as a warning — so the scheduler and the
offline tests run without a database.

Shared contract (do not deviate) — ``insight.pipeline_runs`` columns::

    id text primary key,
    pipeline text not null,
    status text not null,            -- queued | running | success | failed
    started_at timestamptz,
    finished_at timestamptz,
    rows_processed integer,
    error text,
    triggered_by text,
    created_at timestamptz default now()
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The four terminal/known statuses the contract allows.
VALID_STATUSES = frozenset({"queued", "running", "success", "failed"})


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class RunRecord:
    """One tracked pipeline run (mirrors a ``pipeline_runs`` row)."""

    id: str
    pipeline: str
    status: str
    triggered_by: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    rows_processed: int | None = None
    error: str | None = None
    created_at: dt.datetime = field(default_factory=_now)


def _psycopg_available() -> bool:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


class PipelineRunStore:
    """Writes run records to ``<schema>.pipeline_runs``.

    Falls back to an in-memory dict of :class:`RunRecord` when no DSN is
    configured or psycopg is unavailable, so the worker is runnable offline.
    """

    _TABLE = "pipeline_runs"

    def __init__(self, dsn: str | None, schema: str = "insight") -> None:
        self._dsn = dsn
        self._schema = schema
        self._memory: dict[str, RunRecord] = {}
        self._use_postgres = bool(dsn) and _psycopg_available()

        if not dsn:
            logger.warning(
                "POSTGRES_DSN unset — pipeline runs tracked in memory only "
                "(records are lost on exit)."
            )
        elif not _psycopg_available():
            logger.warning(
                "psycopg not installed — pipeline runs tracked in memory only."
            )
        else:
            try:
                self._ensure_table()
            except Exception as exc:  # unreachable DB must not crash startup
                self._use_postgres = False
                logger.warning(
                    "Could not reach Postgres (%s) — falling back to in-memory "
                    "run tracking.",
                    exc,
                )

    @property
    def backend(self) -> str:
        return "postgres" if self._use_postgres else "memory"

    # -- lifecycle ---------------------------------------------------------

    def start(self, pipeline: str, triggered_by: str | None = None) -> str:
        """Open a run in ``running`` state and return its id."""
        run_id = uuid.uuid4().hex
        record = RunRecord(
            id=run_id,
            pipeline=pipeline,
            status="running",
            triggered_by=triggered_by,
            started_at=_now(),
        )
        if self._use_postgres:
            self._insert(record)
        else:
            self._memory[run_id] = record
        logger.info("run %s started: pipeline=%s by=%s", run_id, pipeline, triggered_by)
        return run_id

    def finish(
        self,
        run_id: str,
        status: str,
        rows_processed: int | None = None,
        error: str | None = None,
    ) -> None:
        """Close a run with a terminal status, row count, and optional error."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
        finished_at = _now()
        if self._use_postgres:
            self._update(run_id, status, finished_at, rows_processed, error)
        else:
            record = self._memory.get(run_id)
            if record is None:
                raise KeyError(f"unknown run_id {run_id!r}")
            record.status = status
            record.finished_at = finished_at
            record.rows_processed = rows_processed
            record.error = error
        logger.info(
            "run %s finished: status=%s rows=%s", run_id, status, rows_processed
        )

    def get(self, run_id: str) -> RunRecord | None:
        """Return a run record (in-memory backend only; Postgres reads live in
        the API). Present so tests and callers can assert lifecycle state."""
        if self._use_postgres:
            return self._select(run_id)
        return self._memory.get(run_id)

    # -- postgres ----------------------------------------------------------

    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn)

    def _ensure_table(self) -> None:
        ddl = (
            f"create schema if not exists {self._schema};\n"
            f"create table if not exists {self._schema}.{self._TABLE} (\n"
            "  id text primary key,\n"
            "  pipeline text not null,\n"
            "  status text not null,\n"
            "  started_at timestamptz,\n"
            "  finished_at timestamptz,\n"
            "  rows_processed integer,\n"
            "  error text,\n"
            "  triggered_by text,\n"
            "  created_at timestamptz default now()\n"
            ");"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(ddl)
            conn.commit()

    def _insert(self, record: RunRecord) -> None:
        sql = (
            f"insert into {self._schema}.{self._TABLE} "
            "(id, pipeline, status, started_at, triggered_by, created_at) "
            "values (%s, %s, %s, %s, %s, %s)"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    record.id,
                    record.pipeline,
                    record.status,
                    record.started_at,
                    record.triggered_by,
                    record.created_at,
                ),
            )
            conn.commit()

    def _update(
        self,
        run_id: str,
        status: str,
        finished_at: dt.datetime,
        rows_processed: int | None,
        error: str | None,
    ) -> None:
        sql = (
            f"update {self._schema}.{self._TABLE} "
            "set status = %s, finished_at = %s, rows_processed = %s, error = %s "
            "where id = %s"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (status, finished_at, rows_processed, error, run_id))
            conn.commit()

    def _select(self, run_id: str) -> RunRecord | None:
        sql = (
            "select id, pipeline, status, started_at, finished_at, "
            "rows_processed, error, triggered_by, created_at "
            f"from {self._schema}.{self._TABLE} where id = %s"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (run_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return RunRecord(
            id=row[0],
            pipeline=row[1],
            status=row[2],
            started_at=row[3],
            finished_at=row[4],
            rows_processed=row[5],
            error=row[6],
            triggered_by=row[7],
            created_at=row[8],
        )
