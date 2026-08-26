"""Scheduler registers the expected jobs with the configured cadence."""

from __future__ import annotations

from worker.config import WorkerSettings
from worker.runs import PipelineRunStore
from worker.scheduler import build_scheduler


def _settings() -> WorkerSettings:
    return WorkerSettings(postgres_dsn=None)


def test_registers_the_recurring_jobs():
    scheduler = build_scheduler(PipelineRunStore(dsn=None), _settings(), blocking=False)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"incremental_sync", "reindex_docs", "dbt_build", "insight_digest"}


def test_insight_digest_is_a_daily_cron():
    settings = WorkerSettings(
        postgres_dsn=None, insight_digest_hour=6, insight_digest_minute=30
    )
    scheduler = build_scheduler(PipelineRunStore(dsn=None), settings, blocking=False)
    job = next(j for j in scheduler.get_jobs() if j.id == "insight_digest")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "6"
    assert fields["minute"] == "30"


def test_interval_jobs_use_configured_minutes():
    settings = WorkerSettings(
        postgres_dsn=None, incremental_sync_minutes=30, reindex_docs_minutes=30
    )
    scheduler = build_scheduler(PipelineRunStore(dsn=None), settings, blocking=False)

    by_id = {job.id: job for job in scheduler.get_jobs()}
    # APScheduler IntervalTrigger stores the interval as a timedelta.
    assert by_id["incremental_sync"].trigger.interval.total_seconds() == 30 * 60
    assert by_id["reindex_docs"].trigger.interval.total_seconds() == 30 * 60


def test_dbt_build_is_a_daily_cron():
    settings = WorkerSettings(postgres_dsn=None, dbt_build_hour=2, dbt_build_minute=0)
    scheduler = build_scheduler(PipelineRunStore(dsn=None), settings, blocking=False)
    dbt = next(j for j in scheduler.get_jobs() if j.id == "dbt_build")
    fields = {f.name: str(f) for f in dbt.trigger.fields}
    assert fields["hour"] == "2"
    assert fields["minute"] == "0"


def test_each_job_has_single_instance_lock():
    scheduler = build_scheduler(PipelineRunStore(dsn=None), _settings(), blocking=False)
    for job in scheduler.get_jobs():
        assert job.max_instances == 1
