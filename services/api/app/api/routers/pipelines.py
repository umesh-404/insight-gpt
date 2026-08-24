"""Pipelines (analyst reads, admin triggers) — doc 06 §3.4.

A thin HTTP surface over the pipeline run store owned by the worker service
(``services/worker``). The four pipelines this API exposes are exactly the four
jobs the worker knows how to execute — ``full_ingest``, ``incremental_sync``,
``dbt_build`` and ``reindex_docs`` — so a trigger here always names something
the worker can actually pick up.

Two modes, one contract:

* **Postgres** (``POSTGRES_DSN`` set) — reads the shared ``insight.pipeline_runs``
  table the worker writes and enqueues a ``queued`` row on trigger. The router
  defensively adds the optional ``stages``/``row_counts`` JSON columns so stage
  detail is *persisted* rather than advertised-but-empty; if the API role may not
  alter the table, the columns are simply absent and stage detail reads back
  empty (never a crash).
* **In-memory** (no DSN) — a self-contained store so the offline stack runs with
  no external database. There is no worker offline, so a triggered run is
  advanced through its stages on a wall-clock simulation; runs created this way
  are explicitly marked ``simulated`` in ``row_counts``.

Triggering is idempotent per pipeline: if a run is already active the endpoint
returns ``409 conflict`` with the active run id rather than starting a second.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...config import get_settings
from ..deps import rate_limit
from ..errors import ConflictError, DependencyUnavailableError, NotFoundError

router = APIRouter(tags=["pipelines"])

RunStatus = Literal["queued", "running", "success", "failed", "partial"]
ACTIVE: frozenset[str] = frozenset({"queued", "running"})
_KNOWN_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "success", "failed", "partial"}
)

# The worker's job names (services/worker/worker/jobs.py::JOB_NAMES). The API
# must not invent pipeline names the worker cannot run.
JOB_NAMES: tuple[str, ...] = (
    "full_ingest",
    "incremental_sync",
    "dbt_build",
    "reindex_docs",
)

_MAX_MEMORY_RUNS = 500


class StageRecord(BaseModel):
    name: str
    rows_in: int = 0
    rows_out: int = 0
    ms: float = 0.0
    error: str | None = None


class PipelineRunSummary(BaseModel):
    id: str
    pipeline: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None


class PipelineRun(BaseModel):
    id: str
    pipeline: str
    status: RunStatus
    trigger: Literal["manual", "scheduled"]
    started_at: datetime
    finished_at: datetime | None = None
    stages: list[StageRecord] = Field(default_factory=list)
    row_counts: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class Pipeline(BaseModel):
    name: str
    description: str
    schedule: str | None = None
    last_run: PipelineRunSummary | None = None


class RunHandle(BaseModel):
    run_id: str
    status: RunStatus
    pipeline: str
    trigger: Literal["manual", "scheduled"] = "manual"
    href: str


# --- pipeline definitions (mirrors the worker's scheduler cadence) ------------
_PIPELINES: dict[str, Pipeline] = {
    "full_ingest": Pipeline(
        name="full_ingest",
        description="Full reload of the raw.* landing tables from every source.",
        schedule=None,  # manual only — the scheduler never fires a full reload
    ),
    "incremental_sync": Pipeline(
        name="incremental_sync",
        description="Content-hash / watermark sync of changed rows into raw.*.",
        schedule="*/30 * * * *",
    ),
    "dbt_build": Pipeline(
        name="dbt_build",
        description="Run `dbt build` over the warehouse project into the star schema.",
        schedule="0 2 * * *",
    ),
    "reindex_docs": Pipeline(
        name="reindex_docs",
        description="Re-chunk, re-embed, and index documents into the vector store.",
        schedule="*/30 * * * *",
    ),
}

# The stages each job moves through, used for the offline simulation and as the
# canonical stage vocabulary the worker reports against.
_STAGES: dict[str, tuple[str, ...]] = {
    "full_ingest": ("extract", "load_raw", "verify"),
    "incremental_sync": ("watermark", "extract_delta", "load_raw"),
    "dbt_build": ("dbt_deps", "dbt_run", "dbt_test"),
    "reindex_docs": ("load_documents", "chunk", "embed", "upsert"),
}

_RUNS: dict[str, PipelineRun] = {}


def _sim_seconds() -> float:
    """How long an offline simulated run takes (``PIPELINE_SIM_SECONDS``)."""
    try:
        return max(0.0, float(os.getenv("PIPELINE_SIM_SECONDS", "6")))
    except ValueError:
        return 6.0


def _seed_history() -> None:
    """One completed historical run so the offline UI is not empty."""
    if _RUNS:
        return
    base = datetime.now(UTC) - timedelta(hours=6)
    run = PipelineRun(
        id=f"run_{uuid.uuid4().hex[:12]}",
        pipeline="incremental_sync",
        status="success",
        trigger="scheduled",
        started_at=base,
        finished_at=base + timedelta(minutes=4),
        stages=[
            StageRecord(name="watermark", rows_in=0, rows_out=0, ms=120.0),
            StageRecord(name="extract_delta", rows_in=0, rows_out=12040, ms=1820.0),
            StageRecord(name="load_raw", rows_in=12040, rows_out=12040, ms=940.0),
        ],
        row_counts={"rows_processed": 12040},
    )
    _RUNS[run.id] = run


def _advance_simulated_runs() -> None:
    """Move offline runs through queued -> running -> success on wall clock.

    Without this the in-memory store would leave every triggered run ``queued``
    forever, which would make the 409 idempotency guard permanent.
    """
    now = datetime.now(UTC)
    duration = _sim_seconds()
    for run in _RUNS.values():
        if run.status not in ACTIVE or not run.row_counts.get("simulated"):
            continue
        elapsed = (now - run.started_at).total_seconds()
        if elapsed >= duration:
            names = _STAGES.get(run.pipeline, ("run",))
            per_stage = (duration * 1000.0) / max(1, len(names))
            run.stages = [
                StageRecord(name=n, rows_in=0, rows_out=0, ms=round(per_stage, 2))
                for n in names
            ]
            run.status = "success"
            run.finished_at = run.started_at + timedelta(seconds=duration)
        elif elapsed > 0:
            run.status = "running"


def _trim_memory_runs() -> None:
    if len(_RUNS) <= _MAX_MEMORY_RUNS:
        return
    for rid in sorted(_RUNS, key=lambda r: _RUNS[r].started_at)[: len(_RUNS) - _MAX_MEMORY_RUNS]:
        _RUNS.pop(rid, None)


def _summary(run: PipelineRun) -> PipelineRunSummary:
    return PipelineRunSummary(
        id=run.id, pipeline=run.pipeline, status=run.status,
        started_at=run.started_at, finished_at=run.finished_at,
    )


# --- shared Postgres run store (worker writes; this router reads) -------------
# Column order for every SELECT below; keep in lockstep with ``_row_to_run``.
_BASE_COLS = (
    "id", "pipeline", "status", "started_at", "finished_at",
    "rows_processed", "error", "triggered_by", "created_at",
)
_DETAIL_COLS = ("stages", "row_counts")

_SCHEMA = "insight"
_TABLE = "pipeline_runs"
_QUALIFIED = f"{_SCHEMA}.{_TABLE}"

# Whether the optional detail columns exist, resolved once per DSN.
_DETAIL_READY: dict[str, bool] = {}


def _pg_dsn() -> str | None:
    """The shared run-table DSN, or ``None`` to use the in-memory store."""
    return get_settings().postgres_dsn


def reset_state() -> None:
    """Drop cached in-memory runs and column probes (tests)."""
    _RUNS.clear()
    _DETAIL_READY.clear()


def _psycopg():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - psycopg ships with the api extra
        raise DependencyUnavailableError(
            "The pipeline run store needs psycopg, which is not installed."
        ) from exc
    return psycopg


def _connect(dsn: str):
    psycopg = _psycopg()
    try:
        return psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    except Exception as exc:
        raise DependencyUnavailableError(
            "The pipeline run store is not reachable right now."
        ) from exc


def _has_detail(dsn: str) -> bool:
    """Ensure (once) that the JSON detail columns exist; report what we have.

    The worker's DDL predates stage detail, so add the columns defensively. A
    read-only API role simply fails the ALTER — that is fine, we then know the
    columns are absent and read the base contract instead.
    """
    cached = _DETAIL_READY.get(dsn)
    if cached is not None:
        return cached
    ready = False
    with _connect(dsn) as con:
        try:
            con.execute(
                f"ALTER TABLE {_QUALIFIED} ADD COLUMN IF NOT EXISTS stages jsonb"
            )
            con.execute(
                f"ALTER TABLE {_QUALIFIED} ADD COLUMN IF NOT EXISTS row_counts jsonb"
            )
        except Exception:  # noqa: BLE001 — insufficient privilege is expected
            pass
        try:
            row = con.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s AND column_name = ANY(%s)",
                [_SCHEMA, _TABLE, list(_DETAIL_COLS)],
            ).fetchone()
            ready = bool(row) and int(row[0]) == len(_DETAIL_COLS)
        except Exception:  # noqa: BLE001 — degrade to the base contract
            ready = False
    _DETAIL_READY[dsn] = ready
    return ready


def _select_cols(detail: bool) -> str:
    cols = list(_BASE_COLS) + (list(_DETAIL_COLS) if detail else [])
    return ", ".join(cols)


def _scheduled(triggered_by: str | None) -> bool:
    """The worker stamps ``schedule``; this API stamps ``api``."""
    return triggered_by is None or triggered_by.lower() in {
        "scheduler", "schedule", "cron", "system",
    }


def _as_status(value: Any) -> RunStatus:
    if value is None:
        return "queued"
    text = str(value).strip().lower()
    return text if text in _KNOWN_STATUSES else "partial"  # type: ignore[return-value]


def _as_json(value: Any) -> Any:
    """psycopg returns jsonb as a Python object; tolerate a raw string too."""
    if value is None:
        return None
    if isinstance(value, str | bytes | bytearray):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _as_stages(value: Any) -> list[StageRecord]:
    payload = _as_json(value)
    if not isinstance(payload, list):
        return []
    stages: list[StageRecord] = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            stages.append(StageRecord.model_validate(item))
        except Exception:  # noqa: BLE001 — a malformed row must not 500 the read
            continue
    return stages


def _as_counts(value: Any, rows_processed: Any) -> dict[str, int]:
    payload = _as_json(value)
    counts: dict[str, int] = {}
    if isinstance(payload, dict):
        for key, raw in payload.items():
            try:
                counts[str(key)] = int(raw)
            except (TypeError, ValueError):
                continue
    if rows_processed is not None and "rows_processed" not in counts:
        with contextlib.suppress(TypeError, ValueError):
            counts["rows_processed"] = int(rows_processed)
    return counts


def _row_to_run(row: tuple) -> PipelineRun | None:
    """Map an ``insight.pipeline_runs`` row onto the API's ``PipelineRun``.

    Every optional column may be NULL in the shared table, so nothing here may
    assume a value is present.
    """
    values = list(row) + [None] * (len(_BASE_COLS) + len(_DETAIL_COLS) - len(row))
    (rid, pipeline, status, started_at, finished_at,
     rows_processed, error, triggered_by, created_at, stages, row_counts) = values[:11]

    if not rid:
        return None  # a row with no id is unusable; skip rather than crash
    return PipelineRun(
        id=str(rid),
        pipeline=str(pipeline) if pipeline else "unknown",
        status=_as_status(status),
        trigger="scheduled" if _scheduled(triggered_by) else "manual",
        started_at=started_at or created_at or datetime.now(UTC),
        finished_at=finished_at,
        stages=_as_stages(stages),
        row_counts=_as_counts(row_counts, rows_processed),
        error=str(error) if error else None,
    )


def _pg_fetch_runs(
    dsn: str,
    pipeline: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> list[PipelineRun]:
    detail = _has_detail(dsn)
    where: list[str] = []
    params: list[object] = []
    if pipeline:
        where.append("pipeline = %s")
        params.append(pipeline)
    if status:
        where.append("status = %s")
        params.append(status)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    sql = (
        f"SELECT {_select_cols(detail)} FROM {_QUALIFIED}{clause} "
        "ORDER BY COALESCE(started_at, created_at) DESC NULLS LAST, id DESC "
        "LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])
    with _connect(dsn) as con:
        rows = con.execute(sql, params).fetchall()
    return [run for run in (_row_to_run(r) for r in rows) if run is not None]


def _pg_fetch_run(dsn: str, run_id: str) -> PipelineRun | None:
    detail = _has_detail(dsn)
    sql = f"SELECT {_select_cols(detail)} FROM {_QUALIFIED} WHERE id = %s"
    with _connect(dsn) as con:
        row = con.execute(sql, [run_id]).fetchone()
    return _row_to_run(row) if row else None


def _pg_active_run(dsn: str, pipeline: str) -> PipelineRun | None:
    detail = _has_detail(dsn)
    sql = (
        f"SELECT {_select_cols(detail)} FROM {_QUALIFIED} "
        "WHERE pipeline = %s AND status IN ('queued', 'running') "
        "ORDER BY COALESCE(started_at, created_at) DESC NULLS LAST LIMIT 1"
    )
    with _connect(dsn) as con:
        row = con.execute(sql, [pipeline]).fetchone()
    return _row_to_run(row) if row else None


def _pg_last_runs(dsn: str, pipelines: list[str]) -> dict[str, PipelineRun]:
    """One query for every pipeline's newest run (no N+1 over the pipeline list)."""
    detail = _has_detail(dsn)
    sql = (
        f"SELECT DISTINCT ON (pipeline) {_select_cols(detail)} FROM {_QUALIFIED} "
        "WHERE pipeline = ANY(%s) "
        "ORDER BY pipeline, COALESCE(started_at, created_at) DESC NULLS LAST"
    )
    with _connect(dsn) as con:
        rows = con.execute(sql, [pipelines]).fetchall()
    out: dict[str, PipelineRun] = {}
    for r in rows:
        run = _row_to_run(r)
        if run is not None:
            out[run.pipeline] = run
    return out


def _pg_enqueue(dsn: str, pipeline: str, triggered_by: str) -> str:
    """Insert a ``queued`` row for the worker to pick up; return its id."""
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    sql = (
        f"INSERT INTO {_QUALIFIED} "
        "(id, pipeline, status, triggered_by, created_at) "
        "VALUES (%s, %s, 'queued', %s, %s)"
    )
    with _connect(dsn) as con:
        con.execute(sql, [run_id, pipeline, triggered_by, now])
    return run_id


def _memory_last_run(pipeline: str) -> PipelineRun | None:
    runs = [r for r in _RUNS.values() if r.pipeline == pipeline]
    return max(runs, key=lambda r: r.started_at) if runs else None


def _require_pipeline(name: str) -> Pipeline:
    pipeline = _PIPELINES.get(name)
    if pipeline is None:
        raise NotFoundError(
            f"No pipeline named {name!r}.", details={"known": list(JOB_NAMES)}
        )
    return pipeline


# --- endpoints ----------------------------------------------------------------
@router.get("/pipelines", response_model=list[Pipeline])
async def list_pipelines(_: object = Depends(require_role(Role.analyst))) -> list[Pipeline]:
    dsn = _pg_dsn()
    names = list(_PIPELINES)
    if dsn:
        last = await run_in_threadpool(_pg_last_runs, dsn, names)
    else:
        _seed_history()
        _advance_simulated_runs()
        last = {n: r for n in names if (r := _memory_last_run(n)) is not None}
    return [
        p.model_copy(
            update={"last_run": _summary(last[p.name]) if p.name in last else None}
        )
        for p in _PIPELINES.values()
    ]


@router.post(
    "/pipelines/{name}/run",
    response_model=RunHandle,
    status_code=202,
    dependencies=[Depends(rate_limit("mutate"))],
)
async def run_pipeline(
    name: str, _: object = Depends(require_role(Role.admin))
) -> RunHandle:
    _require_pipeline(name)
    dsn = _pg_dsn()

    if dsn:
        active = await run_in_threadpool(_pg_active_run, dsn, name)
        if active is not None:
            raise ConflictError(
                f"Pipeline {name!r} already has an active run.",
                details={"run_id": active.id, "status": active.status},
            )
        run_id = await run_in_threadpool(_pg_enqueue, dsn, name, "api")
        return RunHandle(
            run_id=run_id, status="queued", pipeline=name,
            href=f"/api/v1/pipeline-runs/{run_id}",
        )

    _seed_history()
    _advance_simulated_runs()
    active = next(
        (r for r in _RUNS.values() if r.pipeline == name and r.status in ACTIVE), None
    )
    if active is not None:
        raise ConflictError(
            f"Pipeline {name!r} already has an active run.",
            details={"run_id": active.id, "status": active.status},
        )
    run = PipelineRun(
        id=f"run_{uuid.uuid4().hex[:12]}",
        pipeline=name,
        status="queued",
        trigger="manual",
        started_at=datetime.now(UTC),
        # Marks this run as advanced by the offline simulator, not a real worker.
        row_counts={"simulated": 1},
    )
    _RUNS[run.id] = run
    _trim_memory_runs()
    return RunHandle(
        run_id=run.id, status=run.status, pipeline=name,
        href=f"/api/v1/pipeline-runs/{run.id}",
    )


@router.get("/pipeline-runs", response_model=list[PipelineRun])
async def list_runs(
    _: object = Depends(require_role(Role.analyst)),
    pipeline: str | None = Query(default=None),
    status: RunStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[PipelineRun]:
    dsn = _pg_dsn()
    if dsn:
        return await run_in_threadpool(
            _pg_fetch_runs, dsn, pipeline, status, limit, offset
        )

    _seed_history()
    _advance_simulated_runs()
    runs = list(_RUNS.values())
    if pipeline:
        runs = [r for r in runs if r.pipeline == pipeline]
    if status:
        runs = [r for r in runs if r.status == status]
    runs.sort(key=lambda r: (r.started_at, r.id), reverse=True)
    return runs[offset : offset + limit]


@router.get("/pipeline-runs/{run_id}", response_model=PipelineRun)
async def get_run(
    run_id: str, _: object = Depends(require_role(Role.analyst))
) -> PipelineRun:
    dsn = _pg_dsn()
    if dsn:
        run = await run_in_threadpool(_pg_fetch_run, dsn, run_id)
    else:
        _seed_history()
        _advance_simulated_runs()
        run = _RUNS.get(run_id)
    if run is None:
        raise NotFoundError(f"No run with id {run_id!r}.")
    return run
