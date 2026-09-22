"""Durable storage for the data-source registry.

Backends, chosen from the environment (mirroring
:mod:`app.insights.store`):

* **postgres** — ``<schema>.sources`` when a DSN is configured and ``psycopg``
  imports, created defensively if bootstrap has not run.
* **file** — a JSON file, so a Docker-less or offline deployment still keeps its
  registry across restarts.
* **memory** — process-local, the last-resort fallback (and what the tests use).

An unreachable database degrades to file/memory with a warning rather than
failing the request: a source list is not worth a 500.

Why the connection string is **not** in here
--------------------------------------------
A source's ``dsn`` is a live credential. Everything else about a source is
durable, but writing the secret into a JSON file next to the repo (or into a
table any read replica can see) trades a restart-convenience for a standing
credential leak, so this store never receives it. Two consequences, both
deliberate:

* A source registered with a literal DSN keeps it for the life of the process.
  After a restart the row is still there — name, kind, status, history — but the
  credential is gone, and a test returns the existing ``missing_dsn`` result
  rather than pretending. The row says so.
* ``options.dsn_env`` names an environment variable to read the DSN from
  instead. Only the *name* is persisted, so the source survives a restart
  intact and the secret never touches disk. This is the recommended way to
  register a database source.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_TABLE = "sources"

# Option keys that could carry a credential. They are dropped on the way to the
# store even though the API models keep secrets in a separate field — a future
# option must not silently become a persisted secret.
_SECRET_OPTION_KEYS = {"dsn", "password", "passwd", "secret", "token", "api_key"}


class SourceRecord(BaseModel):
    """Everything about a source that is safe to write down."""

    id: str
    name: str
    kind: str
    status: str = "untested"
    last_tested_at: datetime | None = None
    active: bool = True
    location: str | None = None
    detail: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    # The env var the DSN is read from, when the source was registered that way.
    dsn_env: str | None = None
    # True when a literal DSN was supplied. The secret itself is never stored;
    # this only lets the UI explain why a test needs it re-entered.
    had_dsn: bool = False


def _psycopg_available() -> bool:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


def sanitize_options(options: dict[str, Any]) -> dict[str, Any]:
    """Drop anything credential-shaped before an options blob is written down."""
    return {k: v for k, v in options.items() if k.lower() not in _SECRET_OPTION_KEYS}


class SourceStore:
    """Load and persist the source registry across a restart."""

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
        self._lock = threading.RLock()
        self._memory: dict[str, SourceRecord] = {}
        self._backend = "memory"

        if dsn and _psycopg_available():
            try:
                self._ensure_table()
                self._backend = "postgres"
            except Exception as exc:  # noqa: BLE001 — never crash on a cold DB
                logger.warning("source store: Postgres unreachable (%s) — falling back.", exc)
        if self._backend == "memory" and self._file_path is not None:
            self._backend = "file"
            self._load_file()

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def durable(self) -> bool:
        """True when the registry actually survives a restart."""
        return self._backend in ("postgres", "file")

    # -- reads -------------------------------------------------------------

    def all(self) -> list[SourceRecord]:
        """Every record, including soft-deleted ones (the caller filters)."""
        if self._backend == "postgres":
            return self._pg_all()
        with self._lock:
            return list(self._memory.values())

    def is_empty(self) -> bool:
        """True when nothing has ever been written — the cue to seed."""
        return not self.all()

    # -- writes ------------------------------------------------------------

    def upsert(self, record: SourceRecord) -> None:
        if self._backend == "postgres":
            self._pg_upsert(record)
            return
        with self._lock:
            self._memory[record.id] = record
            self._flush_file()

    def upsert_many(self, records: list[SourceRecord]) -> None:
        if self._backend == "postgres":
            for record in records:
                self._pg_upsert(record)
            return
        with self._lock:
            for record in records:
                self._memory[record.id] = record
            self._flush_file()

    def clear(self) -> None:
        """Drop everything (tests)."""
        if self._backend == "postgres":
            with self._connect() as con:
                con.execute(f"DELETE FROM {self._schema}.{_TABLE}")
            return
        with self._lock:
            self._memory.clear()
            self._flush_file()

    # -- file backend ------------------------------------------------------

    def _load_file(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
            self._memory = {r["id"]: SourceRecord.model_validate(r) for r in raw}
        except Exception as exc:  # noqa: BLE001 — a corrupt registry is not fatal
            logger.warning("source store: could not read %s (%s).", self._file_path, exc)

    def _flush_file(self) -> None:
        if self._file_path is None:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [r.model_dump(mode="json") for r in self._memory.values()]
            # Write-then-rename: a crash mid-write must not truncate the registry.
            tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._file_path)
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning("source store: could not write %s (%s).", self._file_path, exc)

    # -- postgres backend --------------------------------------------------

    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn, connect_timeout=5, autocommit=True)

    def _ensure_table(self) -> None:
        with self._connect() as con:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
            con.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._schema}.{_TABLE} (
                    id             text PRIMARY KEY,
                    name           text NOT NULL,
                    kind           text NOT NULL,
                    status         text NOT NULL,
                    last_tested_at timestamptz,
                    active         boolean NOT NULL DEFAULT true,
                    location       text,
                    detail         text,
                    options        jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    dsn_env        text,
                    had_dsn        boolean NOT NULL DEFAULT false
                )
                """
            )

    def _pg_all(self) -> list[SourceRecord]:
        try:
            with self._connect() as con:
                rows = con.execute(
                    f"""SELECT id, name, kind, status, last_tested_at, active,
                               location, detail, options, dsn_env, had_dsn
                        FROM {self._schema}.{_TABLE}"""
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("source store: read failed (%s).", exc)
            return []
        return [
            SourceRecord(
                id=r[0], name=r[1], kind=r[2], status=r[3], last_tested_at=r[4],
                active=r[5], location=r[6], detail=r[7], options=r[8] or {},
                dsn_env=r[9], had_dsn=bool(r[10]),
            )
            for r in rows
        ]

    def _pg_upsert(self, record: SourceRecord) -> None:
        try:
            with self._connect() as con:
                con.execute(
                    f"""
                    INSERT INTO {self._schema}.{_TABLE}
                        (id, name, kind, status, last_tested_at, active,
                         location, detail, options, dsn_env, had_dsn)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        kind = EXCLUDED.kind,
                        status = EXCLUDED.status,
                        last_tested_at = EXCLUDED.last_tested_at,
                        active = EXCLUDED.active,
                        location = EXCLUDED.location,
                        detail = EXCLUDED.detail,
                        options = EXCLUDED.options,
                        dsn_env = EXCLUDED.dsn_env,
                        had_dsn = EXCLUDED.had_dsn
                    """,
                    (
                        record.id, record.name, record.kind, record.status,
                        record.last_tested_at, record.active, record.location,
                        record.detail, json.dumps(record.options),
                        record.dsn_env, record.had_dsn,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 — a failed write must not 500 a list
            logger.warning("source store: write failed (%s).", exc)


def _default_store_path() -> Path:
    """``data/sources.json`` anchored at the repo root, not the process cwd.

    The API is normally launched from ``services/api``, so a bare relative path
    would quietly write the registry into ``services/api/data/`` — durable, but
    somewhere nobody looks and nothing ignores. The repo root is located the same
    way the semantic catalog finds ``config/`` (walk up for a known marker).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "semantic_layer.yml").exists():
            return parent / "data" / "sources.json"
    # Standalone install (no repo around it): fall back to the working directory.
    return Path("data") / "sources.json"


def _under_test() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ


def store_from_env(file_path: str | Path | None = None) -> SourceStore:
    """Build the store the way this deployment is configured.

    Durable by default: a plain ``uvicorn`` with no extra environment still keeps
    its registry, because a source list that quietly forgets every restart is the
    bug this store exists to fix. Under pytest the default is memory instead, so
    a test run can never write to (or inherit from) the developer's real
    registry — an explicit ``SOURCES_STORE_PATH`` still wins in both cases.
    """
    dsn = (os.environ.get("POSTGRES_DSN") or "").strip() or None
    schema = os.environ.get("INSIGHT_SCHEMA", "insight")
    configured = os.environ.get("SOURCES_STORE_PATH")
    resolved = file_path or configured or (None if _under_test() else _default_store_path())
    return SourceStore(dsn, schema=schema, file_path=resolved)
