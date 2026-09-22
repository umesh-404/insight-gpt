"""The ``insight_digest`` job — anomaly detection + persistence.

The API's insight engine is stubbed via ``jobs._import_insight_deps`` so the
worker's own suite stays offline (no duckdb / httpx / api package needed). These
tests assert the wiring: the job runs detection, persists through a store, and
reports the count as its tracked row total; a missing engine is recorded as a
failed run, not a crash.
"""

from __future__ import annotations

import pytest

from worker import jobs
from worker.config import WorkerSettings
from worker.jobs import insight_digest, run_job
from worker.runs import PipelineRunStore


class _FakeStore:
    def __init__(self) -> None:
        self.backend = "file"
        self.written: list[object] = []

    def replace_all(self, insights: list[object]) -> int:
        self.written = list(insights)
        return len(insights)


@pytest.fixture
def store() -> PipelineRunStore:
    return PipelineRunStore(dsn=None)


@pytest.fixture
def settings() -> WorkerSettings:
    return WorkerSettings(postgres_dsn=None)


def _stub_deps(monkeypatch, *, insights, capture: dict | None = None):
    fake_store = _FakeStore()

    def _build_engine():
        return object()

    def _detect(_engine):
        return insights

    def _store_from_env(dsn=None, *, schema="insight", file_path=None):
        if capture is not None:
            capture.update(dsn=dsn, schema=schema, file_path=file_path)
        return fake_store

    monkeypatch.setattr(
        jobs, "_import_insight_deps", lambda: (_build_engine, _detect, _store_from_env)
    )
    return fake_store


def test_insight_digest_persists_and_counts(settings, monkeypatch):
    captured: dict = {}
    fake_store = _stub_deps(monkeypatch, insights=["a", "b", "c"], capture=captured)

    written = insight_digest(settings)

    assert written == 3
    assert fake_store.written == ["a", "b", "c"]
    # Offline: no DSN, so the digest is routed to the JSON file fallback.
    assert captured["dsn"] is None
    assert captured["schema"] == settings.insights_schema
    assert captured["file_path"] == settings.insights_file_path


def test_insight_digest_uses_postgres_when_dsn_set(monkeypatch):
    settings = WorkerSettings(postgres_dsn="postgresql://x/y")
    captured: dict = {}
    _stub_deps(monkeypatch, insights=["one"], capture=captured)

    written = insight_digest(settings)

    assert written == 1
    # With a DSN the store targets Postgres; the file fallback is not used.
    assert captured["dsn"] == "postgresql://x/y"
    assert captured["file_path"] is None


def test_insight_digest_is_tracked_as_a_run(store, settings, monkeypatch):
    _stub_deps(monkeypatch, insights=["x", "y"])
    run_id = run_job("insight_digest", store, triggered_by="manual", settings=settings)

    record = store.get(run_id)
    assert record.status == "success"
    assert record.rows_processed == 2


def test_missing_engine_dependency_is_recorded_as_failed(store, settings, monkeypatch):
    def _no_module(_path):
        raise ImportError("no app package here")

    # The lazy import inside _import_insight_deps must surface as a failed run.
    monkeypatch.setattr(jobs, "import_module", _no_module)
    run_id = run_job("insight_digest", store, settings=settings)

    record = store.get(run_id)
    assert record.status == "failed"
    assert "JobDependencyError" in record.error


def test_insight_digest_is_registered():
    assert "insight_digest" in jobs.JOB_NAMES
    assert "insight_digest" in jobs._JOBS
