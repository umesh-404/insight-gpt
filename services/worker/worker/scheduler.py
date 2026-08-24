"""APScheduler wiring — the scheduled cadence (``docs/03`` §5.2).

Registers the recurring jobs and runs them through :func:`worker.jobs.run_job`
so each firing is a tracked ``pipeline_runs`` row:

    incremental_sync  — every N minutes (default 30)
    reindex_docs      — every N minutes (default 30), offset so it does not
                        collide with incremental_sync in the same tick
    dbt_build         — daily at a configured hour

Cadences are env-configurable via :class:`worker.config.WorkerSettings`. A single
scheduler instance with ``max_instances=1`` per job gives the single-instance
execution lock docs/03 §5.2 calls for. Shutdown is graceful: SIGINT/SIGTERM stop
the scheduler and let an in-flight job finish.
"""

from __future__ import annotations

import datetime as dt
import logging
import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from .config import WorkerSettings, get_settings
from .jobs import run_job
from .runs import PipelineRunStore

logger = logging.getLogger(__name__)


def build_scheduler(
    store: PipelineRunStore,
    settings: WorkerSettings | None = None,
    *,
    blocking: bool = True,
):
    """Create a scheduler with the three recurring jobs registered.

    Returns an unstarted scheduler. ``blocking=True`` yields a
    ``BlockingScheduler`` (the ``python -m worker`` loop); ``blocking=False`` a
    ``BackgroundScheduler`` (embeddable / testable without blocking).
    """
    settings = settings or get_settings()
    scheduler: BlockingScheduler | BackgroundScheduler = (
        BlockingScheduler() if blocking else BackgroundScheduler()
    )

    def _job(name: str) -> None:
        run_job(name, store, triggered_by="schedule", settings=settings)

    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=settings.incremental_sync_minutes,
        args=["incremental_sync"],
        id="incremental_sync",
        name="incremental_sync",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=settings.reindex_docs_minutes,
        # Offset the first fire so the two 30-minute jobs stagger.
        next_run_time=dt.datetime.now()
        + dt.timedelta(minutes=settings.reindex_docs_offset_minutes),
        args=["reindex_docs"],
        id="reindex_docs",
        name="reindex_docs",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _job,
        trigger="cron",
        hour=settings.dbt_build_hour,
        minute=settings.dbt_build_minute,
        args=["dbt_build"],
        id="dbt_build",
        name="dbt_build",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info(
        "scheduler configured: incremental_sync/%dm, reindex_docs/%dm (+%dm), "
        "dbt_build @ %02d:%02d — run store backend=%s",
        settings.incremental_sync_minutes,
        settings.reindex_docs_minutes,
        settings.reindex_docs_offset_minutes,
        settings.dbt_build_hour,
        settings.dbt_build_minute,
        store.backend,
    )
    return scheduler


def run_forever(
    store: PipelineRunStore | None = None,
    settings: WorkerSettings | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Start the blocking scheduler and block until interrupted.

    Installs SIGINT/SIGTERM handlers for graceful shutdown. ``stop_event`` is an
    optional hook for embedding/testing: when set, the loop shuts the scheduler
    down cleanly.
    """
    settings = settings or get_settings()
    store = store or PipelineRunStore(settings.postgres_dsn, schema=settings.runs_schema)
    scheduler = build_scheduler(store, settings, blocking=True)

    def _shutdown(*_: object) -> None:
        logger.info("shutdown signal received — stopping scheduler")
        if scheduler.running:
            scheduler.shutdown(wait=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):  # not on the main thread / unsupported
            logger.debug("could not install handler for %s", sig)

    if stop_event is not None:
        threading.Thread(
            target=_wait_and_stop, args=(stop_event, scheduler), daemon=True
        ).start()

    logger.info("worker scheduler starting")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        _shutdown()


def _wait_and_stop(stop_event: threading.Event, scheduler: BlockingScheduler) -> None:
    stop_event.wait()
    if scheduler.running:
        scheduler.shutdown(wait=False)
