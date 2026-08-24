"""The four pipeline jobs, each wrapped in a tracked run (``docs/03`` §5.1).

Every job goes through :func:`run_job`, which opens a ``pipeline_runs`` record,
executes the work, and closes the record with ``success`` + a row count or
``failed`` + the captured error. Failures never propagate out of the wrapper —
a job that raises is recorded as failed and the scheduler loop keeps running.

Business logic lives in the sibling services, imported **lazily** inside each job
so the worker package imports cleanly even where ingestion / retrieval are not
installed. A missing dependency raises a clear :class:`JobDependencyError`, which
is recorded as a failed run like any other error.

Jobs:
    full_ingest       -> services.ingestion full reload of raw.*
    incremental_sync  -> services.ingestion content-hash / watermark sync
    dbt_build         -> `dbt build` over the warehouse project (subprocess)
    reindex_docs      -> retrieval indexer: re-chunk / re-embed into Qdrant
"""

from __future__ import annotations

import logging
import subprocess
import traceback
from collections.abc import Callable
from importlib import import_module

from .config import WorkerSettings, get_settings
from .runs import PipelineRunStore

logger = logging.getLogger(__name__)

JOB_NAMES = ("full_ingest", "incremental_sync", "dbt_build", "reindex_docs")

_ERROR_MAX = 4000  # truncate captured errors so one run can't bloat the table


class JobDependencyError(RuntimeError):
    """A job's sibling service (ingestion / retrieval) is not importable."""


class JobExecutionError(RuntimeError):
    """A job ran but failed (non-zero dbt build, indexer error, ...)."""


# --------------------------------------------------------------------------
# individual jobs — each returns an int row/chunk count and raises on failure
# --------------------------------------------------------------------------


def _import_ingestion_run() -> Callable[..., object]:
    """Locate the ingestion entrypoint, importable either as ``services.ingestion``
    (package layout) or as the top-level ``ingestion`` package."""
    for module_path in ("services.ingestion.run", "ingestion.run"):
        try:
            module = import_module(module_path)
        except ImportError:
            continue
        run = getattr(module, "run", None)
        if callable(run):
            return run
    raise JobDependencyError(
        "ingestion service not importable (tried services.ingestion.run and "
        "ingestion.run) — is services/ingestion installed?"
    )


def _run_ingestion(job: str, source: str = "all") -> int:
    run = _import_ingestion_run()
    stats = run(job, source)
    # RunStats exposes rows_loaded; be defensive about the attribute name.
    rows = getattr(stats, "rows_loaded", None)
    status = getattr(stats, "status", "success")
    if status == "failed":
        raise JobExecutionError(f"ingestion {job} reported status=failed")
    return int(rows or 0)


def full_ingest(settings: WorkerSettings) -> int:
    return _run_ingestion("full_ingest", "all")


def incremental_sync(settings: WorkerSettings) -> int:
    return _run_ingestion("incremental_sync", "all")


def dbt_build(settings: WorkerSettings) -> int:
    """Shell out to ``dbt build`` over the warehouse project, capturing output."""
    project_dir = settings.dbt_project_dir
    if not project_dir.exists():
        raise JobDependencyError(f"dbt project dir not found: {project_dir}")
    cmd = [
        settings.dbt_executable,
        "build",
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(settings.dbt_profiles_dir),
    ]
    logger.info("dbt_build: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.dbt_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise JobDependencyError(
            f"dbt executable not found: {settings.dbt_executable!r}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise JobExecutionError(
            f"dbt build timed out after {settings.dbt_timeout_seconds}s"
        ) from exc

    if proc.returncode != 0:
        tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
        raise JobExecutionError(
            f"dbt build exited {proc.returncode}:\n{tail.strip()}"
        )
    logger.info("dbt_build ok:\n%s", (proc.stdout or "").strip()[-1500:])
    # dbt does not hand us a clean row count; the run is tracked by status.
    return 0


def reindex_docs(settings: WorkerSettings) -> int:
    """Re-chunk / re-embed documents into Qdrant via the retrieval indexer.

    Needs live Qdrant + Ollama at runtime; those failures surface as a failed
    run. Returns the number of chunks written.
    """
    try:
        cli = import_module("retrieval.cli")
        indexer_mod = import_module("retrieval.indexer")
        store_mod = import_module("retrieval.store")
        embedder_mod = import_module("retrieval.embedder")
        sample_mod = import_module("retrieval.sample_docs")
        models_mod = import_module("retrieval.models")
    except ImportError as exc:
        raise JobDependencyError(
            "retrieval service not importable — is services/retrieval installed?"
        ) from exc

    cfg = cli.load_config(None)
    if settings.reindex_source == "samples":
        docs = [models_mod.Document.from_dict(d) for d in sample_mod.get_sample_documents()]
    else:
        docs = indexer_mod.load_documents(_as_path(settings.reindex_source))

    store = store_mod.Store(cfg)
    store.ensure_collection()
    with embedder_mod.Embedder(cfg.embedding) as embedder:
        embedder.health()
        stats = indexer_mod.Indexer(cfg, store, embedder).index_documents(docs)

    if stats.errors:
        raise JobExecutionError(
            f"reindex had {len(stats.errors)} document error(s): {stats.errors[0]}"
        )
    return int(stats.chunks)


def _as_path(value: str):
    from pathlib import Path

    return Path(value)


# job name -> callable(settings) -> int rows
_JOBS: dict[str, Callable[[WorkerSettings], int]] = {
    "full_ingest": full_ingest,
    "incremental_sync": incremental_sync,
    "dbt_build": dbt_build,
    "reindex_docs": reindex_docs,
}


# --------------------------------------------------------------------------
# the wrapper: open a run, execute, close it success/failed
# --------------------------------------------------------------------------


def run_job(
    name: str,
    store: PipelineRunStore,
    triggered_by: str = "schedule",
    settings: WorkerSettings | None = None,
) -> str:
    """Execute one job inside a tracked run and return the run id.

    Records ``success`` + row count on completion, or ``failed`` + the captured
    error on any exception. Never raises for an in-job failure — the scheduler
    loop must survive a bad run — but does raise for an unknown job name.
    """
    if name not in _JOBS:
        raise ValueError(f"unknown job {name!r}; expected one of {list(_JOBS)}")
    settings = settings or get_settings()

    run_id = store.start(name, triggered_by=triggered_by)
    try:
        rows = _JOBS[name](settings)
    except Exception as exc:  # noqa: BLE001 — record any failure, keep looping
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"[:_ERROR_MAX]
        logger.exception("job %s failed", name)
        store.finish(run_id, "failed", rows_processed=None, error=error)
    else:
        store.finish(run_id, "success", rows_processed=rows, error=None)
    return run_id
