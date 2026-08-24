"""Raw-schema loader: idempotent, content-hash incremental (``docs/03`` §6).

Lands redacted records into the Postgres ``raw`` schema using **delete-then-write
per source unit**, so every run converges to the same state instead of
duplicating — the exact practice rememory's ``pipeline.py`` uses (delete before
upsert so a shrunk source leaves no stale tail). A small ``raw._ingest_state``
table records the last content hash per unit, so an unchanged unit is skipped
and counted rather than reloaded.

Everything degrades cleanly when psycopg / Postgres is unavailable: the loader
reports ``available == False`` and its load calls become counted no-ops with a
clear message, so the generator and the offline tests still run end to end.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .connectors.base import Record

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Ingestion metadata columns added to every raw table (docs/02 §3).
_META_COLUMNS = ("_loaded_at", "_source", "_content_hash", "_batch_id")


@dataclass
class LoadResult:
    source: str
    unit_id: str
    table: str
    status: str  # loaded | skipped_unchanged | skipped_unavailable
    rows_loaded: int = 0
    message: str = ""


def psycopg_available() -> bool:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


def _safe_ident(name: str) -> str:
    """Guard against SQL injection via table/column names (all identifiers are
    our own, but validate anyway — defense in depth)."""
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


class RawLoader:
    """Lands records into ``<schema>.<unit_id>`` with metadata + state tracking."""

    def __init__(self, dsn: str | None, schema: str = "raw"):
        self._dsn = dsn
        self._schema = _safe_ident(schema)

    @property
    def available(self) -> bool:
        return bool(self._dsn) and psycopg_available()

    def unavailable_reason(self) -> str:
        if not self._dsn:
            return "no POSTGRES_DSN configured"
        if not psycopg_available():
            return "psycopg not installed"
        return ""

    # ---- public API ----------------------------------------------------------
    def load_records(
        self,
        source: str,
        unit_id: str,
        columns: Sequence[str],
        rows: list[Record],
        fingerprint: str,
        batch_id: str,
        force: bool = False,
    ) -> LoadResult:
        table = _safe_ident(unit_id)
        if not self.available:
            return LoadResult(
                source=source,
                unit_id=unit_id,
                table=f"{self._schema}.{table}",
                status="skipped_unavailable",
                message=self.unavailable_reason(),
            )
        return self._load_with_db(
            source, unit_id, table, columns, rows, fingerprint, batch_id, force
        )

    # ---- database path -------------------------------------------------------
    def _load_with_db(
        self,
        source: str,
        unit_id: str,
        table: str,
        columns: Sequence[str],
        rows: list[Record],
        fingerprint: str,
        batch_id: str,
        force: bool,
    ) -> LoadResult:
        import psycopg
        from psycopg import sql

        cols = [_safe_ident(c) for c in columns]
        fq = sql.Identifier(self._schema, table)
        with psycopg.connect(self._dsn, autocommit=False) as con:
            self._ensure_schema(con)
            if not force and self._stored_fingerprint(con, source, unit_id) == fingerprint:
                return LoadResult(
                    source=source,
                    unit_id=unit_id,
                    table=f"{self._schema}.{table}",
                    status="skipped_unchanged",
                    message="content hash unchanged",
                )
            self._ensure_table(con, fq, cols)
            # delete-then-write for this source unit (idempotent re-run): the
            # table holds exactly this unit, so replacing this source's rows
            # leaves no stale tail even if the source shrank.
            con.execute(sql.SQL("DELETE FROM {} WHERE _source = %s").format(fq), (source,))
            loaded_at = dt.datetime.now(dt.UTC).isoformat()
            all_cols = [*cols, *(_safe_ident(c) for c in _META_COLUMNS)]
            placeholders = sql.SQL(", ").join(sql.Placeholder() * len(all_cols))
            insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                fq,
                sql.SQL(", ").join(sql.Identifier(c) for c in all_cols),
                placeholders,
            )
            with con.cursor() as cur:
                for row in rows:
                    values = [_as_text(row.get(c)) for c in columns]
                    values.extend([loaded_at, source, fingerprint, batch_id])
                    cur.execute(insert, values)
            self._record_state(con, source, unit_id, fingerprint, batch_id, loaded_at)
            con.commit()
        return LoadResult(
            source=source,
            unit_id=unit_id,
            table=f"{self._schema}.{table}",
            status="loaded",
            rows_loaded=len(rows),
        )

    def _ensure_schema(self, con) -> None:
        from psycopg import sql

        con.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema))
        )
        con.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {}._ingest_state ("
                "  source text NOT NULL,"
                "  unit_id text NOT NULL,"
                "  content_hash text NOT NULL,"
                "  batch_id text,"
                "  loaded_at timestamptz,"
                "  PRIMARY KEY (source, unit_id))"
            ).format(sql.Identifier(self._schema))
        )

    def _ensure_table(self, con, fq, cols: list[str]) -> None:
        from psycopg import sql

        col_defs = [sql.SQL("{} text").format(sql.Identifier(c)) for c in cols]
        col_defs.append(sql.SQL("_loaded_at timestamptz"))
        col_defs.append(sql.SQL("_source text"))
        col_defs.append(sql.SQL("_content_hash text"))
        col_defs.append(sql.SQL("_batch_id text"))
        con.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                fq, sql.SQL(", ").join(col_defs)
            )
        )
        # A source that GAINED a column would otherwise fail every load from now
        # on ("column does not exist") — which is exactly the case docs/03 §3.3
        # says a full ingest recovers from. Raw is a landing zone: widen it.
        # Columns are only added, never dropped, so an older column keeps its
        # history and staging decides what to read.
        for col in cols:
            con.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} text").format(
                    fq, sql.Identifier(col)
                )
            )

    def _stored_fingerprint(self, con, source: str, unit_id: str) -> str | None:
        from psycopg import sql

        cur = con.execute(
            sql.SQL(
                "SELECT content_hash FROM {}._ingest_state WHERE source = %s AND unit_id = %s"
            ).format(sql.Identifier(self._schema)),
            (source, unit_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def _record_state(
        self, con, source: str, unit_id: str, fingerprint: str, batch_id: str, loaded_at: str
    ) -> None:
        from psycopg import sql

        con.execute(
            sql.SQL(
                "INSERT INTO {}._ingest_state (source, unit_id, content_hash, batch_id, loaded_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (source, unit_id) DO UPDATE SET "
                "  content_hash = EXCLUDED.content_hash,"
                "  batch_id = EXCLUDED.batch_id,"
                "  loaded_at = EXCLUDED.loaded_at"
            ).format(sql.Identifier(self._schema)),
            (source, unit_id, fingerprint, batch_id, loaded_at),
        )


def _as_text(value: object) -> str | None:
    """Land loosely: everything becomes text in ``raw`` (staging casts)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
