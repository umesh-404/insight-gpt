"""Job wrapper: records success + row counts, and a failed run on exception.

Ingestion / retrieval are stubbed via ``worker.jobs._JOBS`` so no live services
are needed."""

from __future__ import annotations

import pytest

from worker import jobs
from worker.config import WorkerSettings
from worker.jobs import run_job
from worker.runs import PipelineRunStore


@pytest.fixture
def store() -> PipelineRunStore:
    return PipelineRunStore(dsn=None)


@pytest.fixture
def settings() -> WorkerSettings:
    return WorkerSettings(postgres_dsn=None)


def test_run_job_records_success_with_rows(store, settings, monkeypatch):
    monkeypatch.setitem(jobs._JOBS, "full_ingest", lambda _s: 123)
    run_id = run_job("full_ingest", store, triggered_by="manual", settings=settings)

    record = store.get(run_id)
    assert record.status == "success"
    assert record.rows_processed == 123
    assert record.error is None
    assert record.triggered_by == "manual"


def test_run_job_records_failed_run_on_exception(store, settings, monkeypatch):
    def _boom(_s):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(jobs._JOBS, "reindex_docs", _boom)
    run_id = run_job("reindex_docs", store, settings=settings)

    record = store.get(run_id)
    assert record.status == "failed"
    assert record.rows_processed is None
    assert "kaboom" in record.error
    assert "RuntimeError" in record.error


def test_run_job_does_not_propagate_job_exceptions(store, settings, monkeypatch):
    # A failing job must be recorded, not raised — the scheduler loop survives.
    def _boom(_s):
        raise ValueError("bad")

    monkeypatch.setitem(jobs._JOBS, "dbt_build", _boom)
    # Should not raise:
    run_id = run_job("dbt_build", store, settings=settings)
    assert store.get(run_id).status == "failed"


def test_run_job_unknown_name_raises(store, settings):
    with pytest.raises(ValueError):
        run_job("not_a_job", store, settings=settings)


def test_all_job_names_are_registered():
    assert set(jobs.JOB_NAMES) == set(jobs._JOBS)


def test_missing_ingestion_dependency_is_recorded_as_failed(store, settings, monkeypatch):
    def _no_module(_path):
        raise ImportError("no ingestion here")

    monkeypatch.setattr(jobs, "import_module", _no_module)
    run_id = run_job("full_ingest", store, settings=settings)

    record = store.get(run_id)
    assert record.status == "failed"
    assert "JobDependencyError" in record.error
