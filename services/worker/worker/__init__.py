"""InsightGPT worker — the lightweight APScheduler orchestrator.

Runs the ELT / reindex pipelines on a schedule and records every execution in
Postgres ``insight.pipeline_runs`` (``docs/03-ingestion-etl.md`` §5). It owns no
business logic of its own: each job lazily calls into ``services/ingestion`` or
``services/retrieval``, or shells out to ``dbt``, and wraps the call in a tracked
run so status, timings, and row counts are always captured — even on failure.

Everything degrades to run offline: with ``POSTGRES_DSN`` unset the run store
keeps records in memory (with a warning), and a job whose dependency is not
installed fails cleanly with a clear message rather than crashing the loop.
"""

from __future__ import annotations

from .config import WorkerSettings, get_settings
from .jobs import JOB_NAMES, run_job
from .runs import PipelineRunStore, RunRecord

__all__ = [
    "JOB_NAMES",
    "PipelineRunStore",
    "RunRecord",
    "WorkerSettings",
    "get_settings",
    "run_job",
]
