"""Persistence for generated insights.

Backends, chosen at construction from the environment:

* **postgres** — when a DSN is supplied and ``psycopg`` is importable, insights
  land in ``<schema>.insights`` (created defensively if bootstrap has not).
  The full record is kept as JSON alongside a few promoted columns for cheap
  ordering/filtering.
* **file** — a JSON file, so the worker can persist offline across runs.
* **memory** — a process-local dict, the last-resort fallback.

The store is deliberately forgiving: an unreachable database degrades to the
file/memory backend with a warning rather than crashing the caller. Reads are
always newest-first.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .models import Insight

logger = logging.getLogger(__name__)

_TABLE = "insights"


def _psycopg_available() -> bool:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


class InsightStore:
    """Upsert + list insights across a Postgres / file / memory backend."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        schema: str = "insight",
        file_path: str | Path | None = None,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._file_path = Path(file_path) if file_path else None
        self._lock = threading.Lock()
        self._memory: dict[str, Insight] = {}
        self._backend = "memory"

        if dsn and _psycopg_available():
            try:
                self._ensure_table()
                self._backend = "postgres"
            except Exception as exc:  # noqa: BLE001 — never crash on a cold DB
                logger.warning(
                    "insight store: Postgres unreachable (%s) — falling back.", exc
                )
        if self._backend == "memory" and self._file_path is not None:
            self._backend = "file"
            self._load_file()

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def available(self) -> bool:
        """True when insights are durably persisted (Postgres or a file)."""
        return self._backend in ("postgres", "file")

    # -- writes ------------------------------------------------------------

    def replace_all(self, insights: list[Insight]) -> int:
        """Overwrite the stored set with ``insights``; return the count written."""
        if self._backend == "postgres":
            self._pg_replace_all(insights)
        else:
            with self._lock:
                self._memory = {i.id: i for i in insights}
                self._flush_file()
        return len(insights)

    def upsert(self, insight: Insight) -> None:
        if self._backend == "postgres":
            self._pg_upsert(insight)
        else:
            with self._lock:
                self._memory[insight.id] = insight
                self._flush_file()

    # -- reads -------------------------------------------------------------

    def list(self, *, limit: int = 50, offset: int = 0) -> tuple[list[Insight], int]:
        """Return ``(page, total)`` newest-first."""
        items = self._all_sorted()
        return items[offset : offset + limit], len(items)

    def get(self, insight_id: str) -> Insight | None:
        for item in self._all_sorted():
            if item.id == insight_id:
                return item
        return None

    def _all_sorted(self) -> list[Insight]:
        if self._backend == "postgres":
            items = self._pg_all()
        else:
            with self._lock:
                items = list(self._memory.values())
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items

    # -- file backend ------------------------------------------------------

    def _load_file(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
            self._memory = {r["id"]: Insight.model_validate(r) for r in raw}
        except Exception as exc:  # noqa: BLE001 — a corrupt cache is not fatal
            logger.warning("insight store: could not read %s (%s).", self._file_path, exc)

    def _flush_file(self) -> None:
        if self._backend != "file" or self._file_path is None:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [i.model_dump(mode="json") for i in self._memory.values()]
        self._file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- postgres backend --------------------------------------------------

    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn)

    def _ensure_table(self) -> None:
        ddl = (
            f"create schema if not exists {self._schema};\n"
            f"create table if not exists {self._schema}.{_TABLE} (\n"
            "  id text primary key,\n"
            "  metric text not null,\n"
            "  period text,\n"
            "  severity text,\n"
            "  change_pct double precision,\n"
            "  created_at timestamptz default now(),\n"
            "  payload jsonb not null\n"
            ");"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(ddl)
            conn.commit()

    def _pg_upsert(self, insight: Insight) -> None:
        sql = (
            f"insert into {self._schema}.{_TABLE} "
            "(id, metric, period, severity, change_pct, created_at, payload) "
            "values (%s, %s, %s, %s, %s, %s, %s)\n"
            "on conflict (id) do update set metric = excluded.metric, "
            "period = excluded.period, severity = excluded.severity, "
            "change_pct = excluded.change_pct, created_at = excluded.created_at, "
            "payload = excluded.payload"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    insight.id,
                    insight.metric,
                    insight.period,
                    insight.severity,
                    insight.change_pct,
                    insight.created_at,
                    json.dumps(insight.model_dump(mode="json")),
                ),
            )
            conn.commit()

    def _pg_replace_all(self, insights: list[Insight]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"delete from {self._schema}.{_TABLE}")
            for insight in insights:
                cur.execute(
                    f"insert into {self._schema}.{_TABLE} "
                    "(id, metric, period, severity, change_pct, created_at, payload) "
                    "values (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        insight.id,
                        insight.metric,
                        insight.period,
                        insight.severity,
                        insight.change_pct,
                        insight.created_at,
                        json.dumps(insight.model_dump(mode="json")),
                    ),
                )
            conn.commit()

    def _pg_all(self) -> list[Insight]:
        sql = f"select payload from {self._schema}.{_TABLE} order by created_at desc"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        out: list[Insight] = []
        for (payload,) in rows:
            data = payload if isinstance(payload, dict) else json.loads(payload)
            out.append(Insight.model_validate(data))
        return out


def store_from_env(
    dsn: str | None = None, *, schema: str = "insight", file_path: str | Path | None = None
) -> InsightStore:
    """Build a store, reading ``POSTGRES_DSN`` when no DSN is passed explicitly."""
    resolved = dsn or os.getenv("POSTGRES_DSN")
    return InsightStore(resolved, schema=schema, file_path=file_path)
