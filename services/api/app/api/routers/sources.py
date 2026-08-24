"""Data sources (admin) — doc 06 §3.3.

CRUD over an in-process registry. Secrets (``dsn``) are accepted on write, held
as a ``SecretStr``, and never returned on read or written to a log or an error
message. ``DELETE`` is a soft-delete that retains the record for audit.

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


def reset_state() -> None:
    """Clear the registry (tests)."""
    _SOURCES.clear()


def _public(s: _StoredSource) -> Source:
    return Source(
        id=s.id, name=s.name, kind=s.kind, status=s.status,
        last_tested_at=s.last_tested_at, active=s.active,
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
        raise BadRequestError(f"A {body.kind} source requires a 'dsn'.")
    if body.kind in _PATH_KINDS and not _resolve_path(body.options):
        raise BadRequestError(
            f"A {body.kind} source requires a 'path' option.",
            details={"expected_option": "path"},
        )


def _require(source_id: str) -> _StoredSource:
    stored = _SOURCES.get(source_id)
    if stored is None or not stored.active:
        raise NotFoundError(f"No source with id {source_id!r}.")
    return stored


# --- endpoints ----------------------------------------------------------------
@router.get("/sources", response_model=list[Source])
async def list_sources(_: object = Depends(require_role(Role.admin))) -> list[Source]:
    return [_public(s) for s in _SOURCES.values() if s.active]


@router.post("/sources", response_model=Source, status_code=201)
async def create_source(
    body: SourceConfig, _: object = Depends(require_role(Role.admin))
) -> Source:
    _validate(body)
    sid = f"src_{uuid.uuid4().hex[:12]}"
    stored = _StoredSource(
        id=sid, name=body.name, kind=body.kind, status="untested",
        dsn=body.dsn, options=dict(body.options),
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
    return result


@router.delete("/sources/{source_id}", status_code=200)
async def delete_source(
    source_id: str, _: object = Depends(require_role(Role.admin))
) -> dict[str, str]:
    stored = _require(source_id)
    stored.active = False  # soft-delete, retains history for audit
    return {"status": "deleted", "id": source_id}
