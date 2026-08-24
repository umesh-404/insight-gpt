"""``python -m worker`` — the worker entrypoints.

    python -m worker            start the scheduler loop + health server
    python -m worker run JOB    run one job once and exit (JOB is one of
                                full_ingest, incremental_sync, dbt_build,
                                reindex_docs)

The scheduler loop blocks until SIGINT/SIGTERM; the one-shot form runs a single
tracked job and exits non-zero if that run failed, so it composes with `make`
targets and CI.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import get_settings
from .health import HealthServer
from .jobs import JOB_NAMES, run_job
from .runs import PipelineRunStore
from .scheduler import run_forever


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _cmd_run(job: str) -> int:
    """Run one job once; exit 0 on success, 1 on a recorded failure."""
    settings = get_settings()
    store = PipelineRunStore(settings.postgres_dsn, schema=settings.runs_schema)
    run_id = run_job(job, store, triggered_by="manual", settings=settings)
    record = store.get(run_id)
    status = record.status if record else "unknown"
    print(f"[worker] job={job} run_id={run_id} status={status}")
    if record is not None and record.error:
        print(record.error.splitlines()[0], file=sys.stderr)
    return 0 if status == "success" else 1


def _cmd_serve() -> int:
    """Start the health server + the scheduler loop (blocks)."""
    settings = get_settings()
    store = PipelineRunStore(settings.postgres_dsn, schema=settings.runs_schema)
    health = HealthServer(port=settings.health_port).start()
    try:
        run_forever(store=store, settings=settings)
    finally:
        health.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="worker", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="run a single job once and exit")
    p_run.add_argument("job", choices=JOB_NAMES, help="job to run")

    args = parser.parse_args(argv)
    if args.command == "run":
        return _cmd_run(args.job)
    return _cmd_serve()


if __name__ == "__main__":
    raise SystemExit(main())
