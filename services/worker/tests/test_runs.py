"""In-memory run-store lifecycle: start -> finish and status transitions."""

from __future__ import annotations

import pytest

from worker.runs import PipelineRunStore


def _store() -> PipelineRunStore:
    # No DSN -> in-memory backend, no live Postgres needed.
    return PipelineRunStore(dsn=None)


def test_store_without_dsn_uses_memory_backend():
    store = _store()
    assert store.backend == "memory"


def test_start_opens_a_running_record():
    store = _store()
    run_id = store.start("full_ingest", triggered_by="manual")

    record = store.get(run_id)
    assert record is not None
    assert record.pipeline == "full_ingest"
    assert record.status == "running"
    assert record.triggered_by == "manual"
    assert record.started_at is not None
    assert record.finished_at is None


def test_finish_success_records_rows_and_timestamp():
    store = _store()
    run_id = store.start("incremental_sync")
    store.finish(run_id, "success", rows_processed=42)

    record = store.get(run_id)
    assert record is not None
    assert record.status == "success"
    assert record.rows_processed == 42
    assert record.error is None
    assert record.finished_at is not None


def test_finish_failed_records_error():
    store = _store()
    run_id = store.start("dbt_build")
    store.finish(run_id, "failed", error="boom")

    record = store.get(run_id)
    assert record is not None
    assert record.status == "failed"
    assert record.error == "boom"
    assert record.rows_processed is None


def test_finish_rejects_invalid_status():
    store = _store()
    run_id = store.start("reindex_docs")
    with pytest.raises(ValueError):
        store.finish(run_id, "done")


def test_finish_unknown_run_id_raises():
    store = _store()
    with pytest.raises(KeyError):
        store.finish("does-not-exist", "success")


def test_run_ids_are_unique():
    store = _store()
    ids = {store.start("full_ingest") for _ in range(50)}
    assert len(ids) == 50
