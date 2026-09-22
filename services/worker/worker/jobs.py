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
    full_ingest       -> services.ingestion full reload of raw.* + publish the
                         redacted document corpus
    incremental_sync  -> services.ingestion content-hash sync (unchanged units
                         are skipped, not reloaded) + republish the corpus
    dbt_build         -> `dbt build` over the warehouse project (subprocess)
    reindex_docs      -> retrieval indexer: re-chunk / re-embed the documents
                         ingestion published, changed-only

The chain is real: ``full_ingest`` / ``incremental_sync`` write
``data/ingested/documents.json`` and ``reindex_docs`` reads exactly that file.
"""

from __future__ import annotations

import logging
import subprocess
import traceback
from collections.abc import Callable
from importlib import import_module
from pathlib import Path

from .config import _REPO_ROOT, WorkerSettings, get_settings
from .runs import PipelineRunStore

logger = logging.getLogger(__name__)

JOB_NAMES = ("full_ingest", "incremental_sync", "dbt_build", "reindex_docs", "insight_digest")

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


def _resolve_reindex_docs(settings: WorkerSettings, models_mod, corpus_mod, sample_mod):
    """Pick the documents to reindex and the state file that tracks them.

    ``REINDEX_SOURCE=ingested`` (the default) reads the corpus
    ``services/ingestion`` published. Its absence is an ERROR, not a cue to
    quietly index six demo documents: a reindex that reports success while the
    real corpus was never touched is the exact failure this wiring exists to
    prevent. ``samples`` is available as an explicit opt-in.
    """
    source = settings.reindex_source
    if source == "samples":
        docs = [models_mod.Document.from_dict(d) for d in sample_mod.get_sample_documents()]
        return docs, "built-in sample documents", None

    path = settings.document_corpus_path if source == "ingested" else Path(source)
    if not path.exists():
        raise JobDependencyError(
            f"document corpus not found: {path}. Run the ingestion document branch "
            "first (`python -m worker run full_ingest`, or "
            "`python -m services.ingestion run --source documents`), or set "
            "REINDEX_SOURCE=samples to index the demo set on purpose."
        )
    return corpus_mod.load_corpus(path), str(path), corpus_mod.state_path_for(path)


def reindex_docs(settings: WorkerSettings) -> int:
    """Re-chunk / re-embed the ingested documents into Qdrant.

    Changed-only by default: documents whose content hash matches the last
    successful run are skipped, and documents that disappeared from the corpus
    have their chunks deleted. Needs live Qdrant + Ollama at runtime; those
    failures surface as a failed run. Returns the number of chunks written.
    """
    try:
        cli = import_module("retrieval.cli")
        indexer_mod = import_module("retrieval.indexer")
        corpus_mod = import_module("retrieval.corpus")
        store_mod = import_module("retrieval.store")
        embedder_mod = import_module("retrieval.embedder")
        sample_mod = import_module("retrieval.sample_docs")
        models_mod = import_module("retrieval.models")
    except ImportError as exc:
        raise JobDependencyError(
            "retrieval service not importable — is services/retrieval installed?"
        ) from exc

    cfg = cli.load_config(None)
    docs, origin, state_path = _resolve_reindex_docs(
        settings, models_mod, corpus_mod, sample_mod
    )
    if state_path is None:
        # The sample set has no corpus directory of its own to keep state in.
        state_path = settings.document_corpus_path.parent / "samples.index_state.json"
    logger.info("reindex_docs: %d documents from %s", len(docs), origin)

    state = corpus_mod.IndexState(state_path, cfg.collection)
    store = store_mod.Store(cfg)
    store.ensure_collection()
    with embedder_mod.Embedder(cfg.embedding) as embedder:
        embedder.health()
        stats = indexer_mod.Indexer(cfg, store, embedder).index_changed(
            docs, state, full=not settings.reindex_changed_only
        )

    logger.info("reindex_docs: %s", stats.summary())
    if stats.errors:
        raise JobExecutionError(
            f"reindex had {len(stats.errors)} document error(s): {stats.errors[0]}"
        )
    return int(stats.chunks)


def _import_insight_deps():
    """Locate the insight-digest building blocks from the sibling ``api`` service.

    The detection logic lives in the API's ``app.insights`` package (shared by
    the API router and this job). It is imported lazily, and the sibling
    ``services/api`` directory is added to ``sys.path`` so ``app`` resolves even
    when the worker is run from its own tree. A missing package (or its heavy
    deps) raises :class:`JobDependencyError`, recorded like any other failure.
    """
    import sys

    api_dir = _REPO_ROOT / "services" / "api"
    if api_dir.exists() and str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))
    try:
        build = import_module("app.engine.build")
        detector = import_module("app.insights.detector")
        store_mod = import_module("app.insights.store")
    except ImportError as exc:
        raise JobDependencyError(
            "insight engine not importable — is services/api installed with its "
            f"dependencies? ({exc})"
        ) from exc
    return build.build_engine, detector.detect_insights, store_mod.store_from_env


def insight_digest(settings: WorkerSettings) -> int:
    """Detect anomalies over the governed metrics and persist the digest.

    Reuses the API's insight engine (catalog + warehouse + retriever) and its
    documented period-over-period detection. Insights are written to the
    ``insight.insights`` table when ``POSTGRES_DSN`` is set, else to a JSON file
    so the digest survives offline runs. Returns the number of insights written.
    """
    build_engine, detect_insights, store_from_env = _import_insight_deps()

    engine = build_engine()
    insights = detect_insights(engine)
    store = store_from_env(
        settings.postgres_dsn,
        schema=settings.insights_schema,
        file_path=None if settings.postgres_dsn else settings.insights_file_path,
    )
    written = store.replace_all(insights)
    logger.info("insight_digest: %d insight(s) written to %s backend", written, store.backend)
    return written


# job name -> callable(settings) -> int rows
_JOBS: dict[str, Callable[[WorkerSettings], int]] = {
    "full_ingest": full_ingest,
    "incremental_sync": incremental_sync,
    "dbt_build": dbt_build,
    "reindex_docs": reindex_docs,
    "insight_digest": insight_digest,
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
