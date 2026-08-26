"""Data sources (admin) — doc 06 §3.3.

CRUD over an in-process registry. Secrets (``dsn``) are accepted on write, held
as a ``SecretStr``, and never returned on read or written to a log or an error
message. ``DELETE`` is a soft-delete that retains the record for audit.

The registry is **seeded from what this deployment actually has** the first time
it is read (see :func:`_ensure_seeded`): the generator's CSV extracts, the
redacted document corpus, and the Postgres warehouse when ``POSTGRES_DSN`` is
set. Nothing is invented — a seed whose path is missing is registered with
``status="error"`` and a ``detail`` saying so, and every seed is deletable like
any hand-registered source.

``POST /sources/{id}/test`` performs a **real** connectivity check for the kind
of source it claims to be, under a bounded timeout:

* ``postgres`` — opens a connection with ``connect_timeout`` and counts the
  user tables it can see via ``information_schema``.
* ``mysql`` — no MySQL driver ships with this service, so the check is an
  explicit TCP reachability probe against the host/port in the DSN; the result
  says exactly what was and was not verified.
* ``csv`` / ``excel`` — resolves the configured path and checks it exists and is
  readable, counting the matching workbook/CSV files.
* ``documents`` — checks the configured folder exists, is a directory, and is
  readable, counting the documents in it.

Every failure comes back as a structured ``TestResult`` with ``ok=false`` rather
than an exception, and every message is scrubbed of the source's secrets.
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ..deps import rate_limit
from ..errors import BadRequestError, NotFoundError

router = APIRouter(tags=["sources"])

SourceKind = Literal["postgres", "mysql", "csv", "excel", "documents"]

# Kinds that need a DSN vs. kinds that need a filesystem path.
_DSN_KINDS = {"postgres", "mysql"}
_PATH_KINDS = {"csv", "excel", "documents"}
_PATH_OPTION_KEYS = ("path", "directory", "folder", "root")

_DEFAULT_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 30.0
_DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306}
_FILE_GLOBS = {
    "csv": ("*.csv", "*.tsv"),
    "excel": ("*.xlsx", "*.xls", "*.xlsm"),
    "documents": ("*",),
}


class SourceConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: SourceKind
    dsn: SecretStr | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    id: str
    name: str
    kind: str
    status: Literal["ok", "untested", "error"]
    last_tested_at: datetime | None = None
    active: bool = True
    # Non-secret "where does this point" summary: the configured path for file
    # kinds, ``host:port`` for DSN kinds. Credentials never reach this field.
    location: str | None = None
    # Why the source is in its current status — the last probe message, or the
    # reason a seeded source could not be found on disk.
    detail: str | None = None


class _StoredSource(Source):
    dsn: SecretStr | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    ok: bool
    latency_ms: float
    tables_seen: int
    message: str
    # What was actually verified, so "ok" is never ambiguous.
    checked: str = "connectivity"
    error_code: str | None = None


_SOURCES: dict[str, _StoredSource] = {}
_SEEDED = False


def reset_state() -> None:
    """Clear the registry, seeds included (tests)."""
    global _SEEDED
    _SOURCES.clear()
    _SEEDED = False


def _public(s: _StoredSource) -> Source:
    return Source(
        id=s.id, name=s.name, kind=s.kind, status=s.status,
        last_tested_at=s.last_tested_at, active=s.active,
        location=s.location, detail=s.detail,
    )


def _timeout(options: dict[str, Any]) -> float:
    raw = options.get("timeout_s", _DEFAULT_TIMEOUT_S)
    try:
        return max(0.5, min(_MAX_TIMEOUT_S, float(raw)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _secret_parts(dsn: str | None) -> list[str]:
    """Every substring of the DSN that must never appear in output."""
    if not dsn:
        return []
    parts = [dsn]
    try:
        split = urlsplit(dsn)
        if split.password:
            parts.append(split.password)
            parts.append(unquote(split.password))
        if split.username:
            parts.append(split.username)
    except ValueError:
        pass
    # libpq keyword/value form: password=... in a space-separated DSN.
    for token in dsn.split():
        key, sep, value = token.partition("=")
        if sep and key.strip().lower() in {"password", "passfile"} and value:
            parts.append(value)
    return [p for p in parts if p]


def _scrub(text: str, secrets: list[str]) -> str:
    """Remove any secret substring from a message before it leaves the process."""
    out = text
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            out = out.replace(secret, "***")
    return out.strip() or "connection failed"


def _clip(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _resolve_path(options: dict[str, Any]) -> str | None:
    for key in _PATH_OPTION_KEYS:
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _host_port(dsn: str, kind: str) -> tuple[str, int] | None:
    """Best-effort host/port extraction from a URL or libpq keyword DSN."""
    try:
        split = urlsplit(dsn)
        if split.hostname:
            return split.hostname, split.port or _DEFAULT_PORTS.get(kind, 0)
    except ValueError:
        pass
    host, port = None, None
    for token in dsn.split():
        key, sep, value = token.partition("=")
        if not sep:
            continue
        if key.strip().lower() == "host":
            host = value
        elif key.strip().lower() == "port":
            port = value
    if not host:
        return None
    try:
        return host, int(port) if port else _DEFAULT_PORTS.get(kind, 0)
    except ValueError:
        return host, _DEFAULT_PORTS.get(kind, 0)


def _location(kind: str, dsn: str | None, options: dict[str, Any]) -> str | None:
    """A non-secret, displayable summary of where a source points.

    File kinds show their configured path. DSN kinds show only ``host:port`` —
    never the user, the password or the database name — so the value is safe to
    return from a read and to render in the UI.
    """
    if kind in _DSN_KINDS:
        if not dsn:
            return None
        target = _host_port(dsn, kind)
        return f"{target[0]}:{target[1]}" if target else None
    return _resolve_path(options)


# --- the actual probes (synchronous; always called via run_in_threadpool) -----


def _probe_postgres(dsn: str, timeout: float, secrets: list[str]) -> TestResult:
    started = time.perf_counter()
    try:
        import psycopg
    except ImportError:
        return TestResult(
            ok=False, latency_ms=_ms(started), tables_seen=0,
            message="No PostgreSQL driver is installed in this service.",
            checked="driver", error_code="driver_missing",
        )
    try:
        with psycopg.connect(dsn, connect_timeout=int(timeout), autocommit=True) as con:
            con.execute(f"SET statement_timeout = {int(timeout * 1000)}")
            row = con.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — every failure is a structured result
        return TestResult(
            ok=False, latency_ms=_ms(started), tables_seen=0,
            message=_clip(_scrub(f"{type(exc).__name__}: {exc}", secrets)),
            checked="connect", error_code="connect_failed",
        )
    tables = int(row[0]) if row else 0
    return TestResult(
        ok=True, latency_ms=_ms(started), tables_seen=tables,
        message=f"Connected and introspected {tables} table(s).",
        checked="connect+introspect",
    )


def _probe_tcp(dsn: str, kind: str, timeout: float, secrets: list[str]) -> TestResult:
    started = time.perf_counter()
    target = _host_port(dsn, kind)
    if target is None or not target[1]:
        return TestResult(
            ok=False, latency_ms=_ms(started), tables_seen=0,
            message="Could not read a host and port from the configured DSN.",
            checked="dsn", error_code="bad_dsn",
        )
    host, port = target
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return TestResult(
            ok=False, latency_ms=_ms(started), tables_seen=0,
            message=_clip(_scrub(f"{type(exc).__name__}: {exc}", secrets)),
            checked="tcp", error_code="connect_failed",
        )
    return TestResult(
        ok=True, latency_ms=_ms(started), tables_seen=0,
        message=(
            f"Reached {host}:{port}. No {kind} driver is installed here, so schema "
            "introspection was not performed."
        ),
        checked="tcp", error_code=None,
    )


def _probe_path(kind: str, raw_path: str, secrets: list[str]) -> TestResult:
    started = time.perf_counter()
    path = Path(raw_path).expanduser()
    try:
        if not path.exists():
            return TestResult(
                ok=False, latency_ms=_ms(started), tables_seen=0,
                message=f"Path does not exist: {path}",
                checked="filesystem", error_code="not_found",
            )
        if path.is_dir():
            patterns = _FILE_GLOBS.get(kind, ("*",))
            files = [
                p for pattern in patterns for p in sorted(path.glob(pattern)) if p.is_file()
            ]
            # Readability is only proven by actually opening something.
            if files:
                files[0].open("rb").close()
            else:
                next(path.iterdir(), None)
            return TestResult(
                ok=True, latency_ms=_ms(started), tables_seen=len(files),
                message=f"Directory is readable; found {len(files)} matching file(s).",
                checked="filesystem",
            )
        path.open("rb").close()
        return TestResult(
            ok=True, latency_ms=_ms(started), tables_seen=1,
            message=f"File is readable ({path.stat().st_size} bytes).",
            checked="filesystem",
        )
    except OSError as exc:
        return TestResult(
            ok=False, latency_ms=_ms(started), tables_seen=0,
            message=_clip(_scrub(f"{type(exc).__name__}: {exc}", secrets)),
            checked="filesystem", error_code="unreadable",
        )


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _run_probe(stored: _StoredSource) -> TestResult:
    dsn = stored.dsn.get_secret_value() if stored.dsn else None
    secrets = _secret_parts(dsn)
    timeout = _timeout(stored.options)

    if stored.kind in _DSN_KINDS:
        if not dsn:
            return TestResult(
                ok=False, latency_ms=0.0, tables_seen=0,
                message=f"A {stored.kind} source needs a DSN before it can be tested.",
                checked="config", error_code="missing_dsn",
            )
        if stored.kind == "postgres":
            return _probe_postgres(dsn, timeout, secrets)
        return _probe_tcp(dsn, stored.kind, timeout, secrets)

    raw_path = _resolve_path(stored.options)
    if not raw_path:
        return TestResult(
            ok=False, latency_ms=0.0, tables_seen=0,
            message=(
                f"A {stored.kind} source needs a 'path' option pointing at the "
                "file or folder to read."
            ),
            checked="config", error_code="missing_path",
        )
    return _probe_path(stored.kind, raw_path, secrets)


def _validate(body: SourceConfig) -> None:
    if body.kind in _DSN_KINDS and not (body.dsn and body.dsn.get_secret_value().strip()):
        raise BadRequestError(
            f"A {body.kind} source requires a 'dsn'.",
            details={"expected_field": "dsn"},
        )
    if body.kind in _PATH_KINDS and not _resolve_path(body.options):
        raise BadRequestError(
            f"A {body.kind} source requires a 'path' option.",
            details={"expected_option": "path"},
        )


# --- seeding the registry from what this deployment actually has --------------

# services/api/app/api/routers/sources.py -> repo root is five levels up.
_REPO_ROOT = Path(__file__).resolve().parents[5]

# The tables `data/generator` writes as CSV extracts, in load order.
_GENERATED_TABLES = ("customers", "products", "stores", "orders", "order_items", "inventory")


def _generated_dir() -> Path:
    """Where the generator wrote its CSV extracts (``GENERATED_DIR`` overrides)."""
    if override := os.environ.get("GENERATED_DIR"):
        return Path(override)
    return _REPO_ROOT / "data" / "generated"


def _corpus_path() -> Path:
    """The redacted document corpus (``DOCUMENT_CORPUS_PATH`` overrides).

    Same variable the ingestion and worker services read, so relocating the
    hand-off relocates this listing too.
    """
    if override := os.environ.get("DOCUMENT_CORPUS_PATH"):
        return Path(override)
    return _REPO_ROOT / "data" / "ingested" / "documents.json"


def _seed_path_source(sid: str, name: str, kind: SourceKind, path: Path) -> _StoredSource:
    """Register a real path without pretending it is healthy.

    Existence is the only thing checked here — cheap, and honest: a path that is
    present is ``untested`` until someone runs the probe, and a path that is
    absent is an ``error`` that says which path is missing.
    """
    exists = path.exists()
    return _StoredSource(
        id=sid,
        name=name,
        kind=kind,
        status="untested" if exists else "error",
        options={"path": str(path)},
        location=str(path),
        detail=(
            "Registered from this deployment's layout. Run a test to verify it is readable."
            if exists
            else f"Not found on disk: {path}"
        ),
    )


def _seeds() -> list[_StoredSource]:
    """The sources this deployment genuinely has, derived from paths and env."""
    seeded: list[_StoredSource] = []

    generated = _generated_dir()
    for table in _GENERATED_TABLES:
        csv_path = generated / f"{table}.csv"
        seeded.append(
            _seed_path_source(f"src_seed_csv_{table}", f"{table}.csv", "csv", csv_path)
        )

    seeded.append(
        _seed_path_source(
            "src_seed_documents", "document corpus", "documents", _corpus_path()
        )
    )

    dsn = (os.environ.get("POSTGRES_DSN") or "").strip()
    if dsn:
        seeded.append(
            _StoredSource(
                id="src_seed_warehouse",
                name="warehouse (postgres)",
                kind="postgres",
                status="untested",
                dsn=SecretStr(dsn),
                options={},
                location=_location("postgres", dsn, {}),
                detail="Configured via POSTGRES_DSN. Run a test to verify connectivity.",
            )
        )
    return seeded


def _ensure_seeded() -> None:
    """Populate the registry once per process (and once per ``reset_state``).

    Seeding is a one-shot: a seeded source that an admin deletes stays deleted
    instead of reappearing on the next list.
    """
    global _SEEDED
    if _SEEDED:
        return
    _SEEDED = True
    for source in _seeds():
        _SOURCES.setdefault(source.id, source)


def _require(source_id: str) -> _StoredSource:
    _ensure_seeded()
    stored = _SOURCES.get(source_id)
    if stored is None or not stored.active:
        raise NotFoundError(f"No source with id {source_id!r}.")
    return stored


# --- endpoints ----------------------------------------------------------------
@router.get("/sources", response_model=list[Source])
async def list_sources(_: object = Depends(require_role(Role.admin))) -> list[Source]:
    _ensure_seeded()
    return [_public(s) for s in _SOURCES.values() if s.active]


@router.post("/sources", response_model=Source, status_code=201)
async def create_source(
    body: SourceConfig, _: object = Depends(require_role(Role.admin))
) -> Source:
    _ensure_seeded()
    _validate(body)
    sid = f"src_{uuid.uuid4().hex[:12]}"
    options = dict(body.options)
    stored = _StoredSource(
        id=sid, name=body.name, kind=body.kind, status="untested",
        dsn=body.dsn, options=options,
        location=_location(
            body.kind, body.dsn.get_secret_value() if body.dsn else None, options
        ),
        detail="Registered. Run a test to verify connectivity.",
    )
    _SOURCES[sid] = stored
    return _public(stored)


@router.get("/sources/{source_id}", response_model=Source)
async def get_source(
    source_id: str, _: object = Depends(require_role(Role.admin))
) -> Source:
    return _public(_require(source_id))


@router.post(
    "/sources/{source_id}/test",
    response_model=TestResult,
    dependencies=[Depends(rate_limit("mutate"))],
)
async def test_source(
    source_id: str, _: object = Depends(require_role(Role.admin))
) -> TestResult:
    stored = _require(source_id)
    # Probes are blocking sockets / filesystem calls — keep them off the loop.
    result = await run_in_threadpool(_run_probe, stored)
    stored.status = "ok" if result.ok else "error"
    stored.last_tested_at = datetime.now(UTC)
    # ``result.message`` has already been scrubbed of every secret substring.
    stored.detail = result.message
    return result


@router.delete("/sources/{source_id}", status_code=200)
async def delete_source(
    source_id: str, _: object = Depends(require_role(Role.admin))
) -> dict[str, str]:
    stored = _require(source_id)
    stored.active = False  # soft-delete, retains history for audit
    return {"status": "deleted", "id": source_id}
