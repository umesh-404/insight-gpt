"""Pipelines (analyst reads, admin triggers) — doc 06 §3.4.

A thin HTTP surface over an in-memory run store standing in for the APScheduler
worker. Triggering a run is idempotent per pipeline: if a run is already active
the endpoint returns ``409 conflict`` with the active run id rather than
starting a second (doc 06 §3.4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...auth.roles import Role, require_role
from ..errors import ConflictError, NotFoundError

router = APIRouter(tags=["pipelines"])

RunStatus = Literal["queued", "running", "success", "failed", "partial"]
ACTIVE = {"queued", "running"}


class StageRecord(BaseModel):
    name: str
    rows_in: int
    rows_out: int
    ms: float
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


# --- seeded pipeline definitions + run history --------------------------------
_PIPELINES: dict[str, Pipeline] = {
    "retail_elt": Pipeline(
        name="retail_elt",
        description="Land raw retail sources, then dbt-transform into the star schema.",
        schedule="0 2 * * *",
    ),
    "document_index": Pipeline(
        name="document_index",
        description="Chunk, embed, and index support tickets and reviews into Qdrant.",
        schedule="0 3 * * *",
    ),
}
_RUNS: dict[str, PipelineRun] = {}


def _seed_history() -> None:
    if _RUNS:
        return
    base = datetime.now(UTC) - timedelta(hours=6)
    run = PipelineRun(
        id=f"run_{uuid.uuid4().hex[:12]}",
        pipeline="retail_elt",
        status="success",
        trigger="scheduled",
        started_at=base,
        finished_at=base + timedelta(minutes=4),
        stages=[
            StageRecord(name="extract", rows_in=0, rows_out=12040, ms=1820.0),
            StageRecord(name="load_raw", rows_in=12040, rows_out=12040, ms=940.0),
            StageRecord(name="dbt_transform", rows_in=12040, rows_out=11890, ms=6120.0),
        ],
        row_counts={"fact_order_items": 11890, "dim_product": 320},
    )
    _RUNS[run.id] = run


def _summary(run: PipelineRun) -> PipelineRunSummary:
    return PipelineRunSummary(
        id=run.id, pipeline=run.pipeline, status=run.status,
        started_at=run.started_at, finished_at=run.finished_at,
    )


def _last_run(pipeline: str) -> PipelineRun | None:
    runs = [r for r in _RUNS.values() if r.pipeline == pipeline]
    return max(runs, key=lambda r: r.started_at) if runs else None


@router.get("/pipelines", response_model=list[Pipeline])
async def list_pipelines(_: object = Depends(require_role(Role.analyst))) -> list[Pipeline]:
    _seed_history()
    out: list[Pipeline] = []
    for p in _PIPELINES.values():
        last = _last_run(p.name)
        out.append(p.model_copy(update={"last_run": _summary(last) if last else None}))
    return out


@router.post("/pipelines/{name}/run", response_model=RunHandle, status_code=202)
async def run_pipeline(
    name: str, _: object = Depends(require_role(Role.admin))
) -> RunHandle:
    if name not in _PIPELINES:
        raise NotFoundError(f"No pipeline named {name!r}.")
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
    )
    _RUNS[run.id] = run
    return RunHandle(run_id=run.id, status=run.status)


@router.get("/pipeline-runs", response_model=list[PipelineRun])
async def list_runs(
    _: object = Depends(require_role(Role.analyst)),
    pipeline: str | None = Query(default=None),
    status: RunStatus | None = Query(default=None),
) -> list[PipelineRun]:
    _seed_history()
    runs = list(_RUNS.values())
    if pipeline:
        runs = [r for r in runs if r.pipeline == pipeline]
    if status:
        runs = [r for r in runs if r.status == status]
    return sorted(runs, key=lambda r: r.started_at, reverse=True)


@router.get("/pipeline-runs/{run_id}", response_model=PipelineRun)
async def get_run(
    run_id: str, _: object = Depends(require_role(Role.analyst))
) -> PipelineRun:
    run = _RUNS.get(run_id)
    if run is None:
        raise NotFoundError(f"No run with id {run_id!r}.")
    return run
