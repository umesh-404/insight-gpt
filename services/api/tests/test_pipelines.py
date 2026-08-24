"""Pipelines router (doc 06 §3.4) — both the in-memory and the Postgres path.

The Postgres path is exercised with a stubbed connection so the whole file runs
offline: no database is required, but the SQL that would be sent and the row
mapping that would come back are both asserted.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.routers import pipelines as pl  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    pl.reset_state()


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def admin(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {_login(client, 'admin@insightgpt.dev', 'admin-pass')}"}


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> dict[str, str]:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    return {"Authorization": f"Bearer {token}"}


# --- in-memory mode ------------------------------------------------------------


def test_pipelines_are_the_workers_jobs(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.get("/api/v1/pipelines", headers=analyst)
    assert resp.status_code == 200, resp.text
    names = [p["name"] for p in resp.json()]
    assert names == list(pl.JOB_NAMES)


def test_unknown_pipeline_is_404(client: TestClient, admin: dict[str, str]) -> None:
    resp = client.post("/api/v1/pipelines/retail_elt/run", headers=admin)
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["code"] == "not_found"
    assert body["details"]["known"] == list(pl.JOB_NAMES)


def test_trigger_returns_usable_handle_and_is_idempotent(
    client: TestClient, admin: dict[str, str], analyst: dict[str, str]
) -> None:
    resp = client.post("/api/v1/pipelines/dbt_build/run", headers=admin)
    assert resp.status_code == 202, resp.text
    handle = resp.json()
    assert handle["pipeline"] == "dbt_build"
    assert handle["status"] == "queued"
    assert handle["href"] == f"/api/v1/pipeline-runs/{handle['run_id']}"

    # The handle resolves to a real run.
    run = client.get(handle["href"], headers=analyst)
    assert run.status_code == 200
    assert run.json()["pipeline"] == "dbt_build"
    assert run.json()["trigger"] == "manual"

    # A second trigger while it is active conflicts, naming the active run.
    again = client.post("/api/v1/pipelines/dbt_build/run", headers=admin)
    assert again.status_code == 409
    err = again.json()["error"]
    assert err["code"] == "conflict"
    assert err["details"]["run_id"] == handle["run_id"]


def test_simulated_run_completes_with_stages(
    client: TestClient, admin: dict[str, str], analyst: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(pl, "_sim_seconds", lambda: 0.0)
    resp = client.post("/api/v1/pipelines/reindex_docs/run", headers=admin)
    run_id = resp.json()["run_id"]
    detail = client.get(f"/api/v1/pipeline-runs/{run_id}", headers=analyst).json()
    assert detail["status"] == "success"
    assert detail["finished_at"] is not None
    assert [s["name"] for s in detail["stages"]] == list(pl._STAGES["reindex_docs"])
    # ...and the pipeline is triggerable again once it has finished.
    assert client.post("/api/v1/pipelines/reindex_docs/run", headers=admin).status_code == 202


def test_unknown_run_is_404(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.get("/api/v1/pipeline-runs/run_nope", headers=analyst)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_runs_are_paginated_and_newest_first(
    client: TestClient, admin: dict[str, str], analyst: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(pl, "_sim_seconds", lambda: 0.0)
    for name in pl.JOB_NAMES:
        assert client.post(f"/api/v1/pipelines/{name}/run", headers=admin).status_code == 202

    resp = client.get("/api/v1/pipeline-runs", headers=analyst)
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) >= 5  # four triggers + the seeded history
    stamps = [r["started_at"] for r in runs]
    assert stamps == sorted(stamps, reverse=True)

    page = client.get("/api/v1/pipeline-runs?limit=2", headers=analyst).json()
    assert len(page) == 2
    assert page == runs[:2]
    second = client.get("/api/v1/pipeline-runs?limit=2&offset=2", headers=analyst).json()
    assert second == runs[2:4]

    filtered = client.get("/api/v1/pipeline-runs?pipeline=dbt_build", headers=analyst).json()
    assert filtered and all(r["pipeline"] == "dbt_build" for r in filtered)


def test_pagination_bounds_are_validated(client: TestClient, analyst: dict[str, str]) -> None:
    assert client.get("/api/v1/pipeline-runs?limit=0", headers=analyst).status_code == 422
    assert client.get("/api/v1/pipeline-runs?limit=999", headers=analyst).status_code == 422
    assert client.get("/api/v1/pipeline-runs?offset=-1", headers=analyst).status_code == 422


def test_last_run_is_reported_on_the_pipeline(
    client: TestClient, admin: dict[str, str], analyst: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(pl, "_sim_seconds", lambda: 0.0)
    run_id = client.post("/api/v1/pipelines/full_ingest/run", headers=admin).json()["run_id"]
    pipelines = {p["name"]: p for p in client.get("/api/v1/pipelines", headers=analyst).json()}
    assert pipelines["full_ingest"]["last_run"]["id"] == run_id
    assert pipelines["dbt_build"]["last_run"] is None


# --- row mapping: every optional column may be NULL -----------------------------


def test_row_to_run_tolerates_null_columns() -> None:
    created = datetime(2026, 7, 1, 12, tzinfo=UTC)
    run = pl._row_to_run(
        ("run_1", "dbt_build", None, None, None, None, None, None, created, None, None)
    )
    assert run is not None
    assert run.status == "queued"          # NULL status is not yet started
    assert run.started_at == created       # falls back to created_at
    assert run.stages == []
    assert run.row_counts == {}
    assert run.error is None
    assert run.trigger == "scheduled"      # NULL triggered_by means the scheduler


def test_row_to_run_maps_detail_columns() -> None:
    started = datetime(2026, 7, 1, 12, tzinfo=UTC)
    run = pl._row_to_run(
        (
            "run_2", "full_ingest", "success", started, started + timedelta(minutes=2),
            4200, None, "api", started,
            '[{"name": "extract", "rows_in": 0, "rows_out": 4200, "ms": 12.5}]',
            {"fact_order_items": 4200},
        )
    )
    assert run is not None
    assert run.trigger == "manual"
    assert [s.name for s in run.stages] == ["extract"]
    assert run.stages[0].rows_out == 4200
    assert run.row_counts == {"fact_order_items": 4200, "rows_processed": 4200}


def test_row_to_run_skips_unusable_rows_and_clamps_status() -> None:
    assert pl._row_to_run((None, "x", "success", None, None, None, None, None, None)) is None
    weird = pl._row_to_run(("r", None, "exploded", None, None, None, None, None, None))
    assert weird is not None
    assert weird.status == "partial"       # an unknown status is not a crash
    assert weird.pipeline == "unknown"
    assert weird.stages == []


def test_malformed_stage_json_is_ignored() -> None:
    assert pl._as_stages("not json") == []
    assert pl._as_stages('{"not": "a list"}') == []
    assert pl._as_stages('[{"no_name": 1}, {"name": "ok"}]')[0].name == "ok"


# --- Postgres mode (stubbed connection) -----------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Records every statement and replays canned rows."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, list]] = []

    def execute(self, sql: str, params: list | None = None) -> _FakeResult:
        self.statements.append((sql, list(params or [])))
        if "information_schema.columns" in sql:
            return _FakeResult([(2,)])
        if sql.strip().upper().startswith(("ALTER", "INSERT", "SET")):
            return _FakeResult([])
        return _FakeResult(self.rows)

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


@pytest.fixture
def pg(monkeypatch) -> _FakeConn:
    conn = _FakeConn([])
    monkeypatch.setattr(pl, "_pg_dsn", lambda: "postgresql://stub/insight")
    monkeypatch.setattr(pl, "_connect", lambda dsn: conn)
    return conn


def test_pg_fetch_runs_sorts_newest_first_and_paginates(pg: _FakeConn) -> None:
    started = datetime(2026, 7, 1, 12, tzinfo=UTC)
    pg.rows = [
        ("run_1", "dbt_build", "success", started, None, 10, None, "api", started, None, None)
    ]
    runs = pl._pg_fetch_runs("dsn", pipeline="dbt_build", status="success", limit=25, offset=50)
    assert [r.id for r in runs] == ["run_1"]

    select = next(s for s, _ in pg.statements if s.startswith("SELECT id"))
    assert "ORDER BY COALESCE(started_at, created_at) DESC NULLS LAST" in select
    assert "LIMIT %s OFFSET %s" in select
    assert "stages, row_counts" in select  # detail columns are read when present
    params = next(p for s, p in pg.statements if s.startswith("SELECT id"))
    assert params == ["dbt_build", "success", 25, 50]


def test_pg_adds_the_detail_columns_defensively(pg: _FakeConn) -> None:
    assert pl._has_detail("dsn") is True
    alters = [s for s, _ in pg.statements if s.startswith("ALTER")]
    assert any("ADD COLUMN IF NOT EXISTS stages jsonb" in s for s in alters)
    assert any("ADD COLUMN IF NOT EXISTS row_counts jsonb" in s for s in alters)
    # The probe is cached, so a second read does not re-ALTER.
    pl._has_detail("dsn")
    assert len([s for s, _ in pg.statements if s.startswith("ALTER")]) == len(alters)


def test_pg_reads_fall_back_when_detail_columns_are_absent(monkeypatch) -> None:
    conn = _FakeConn([])

    class _NoDetail(_FakeConn):
        def execute(self, sql: str, params: list | None = None) -> _FakeResult:
            self.statements.append((sql, list(params or [])))
            if "information_schema.columns" in sql:
                return _FakeResult([(0,)])
            if sql.strip().upper().startswith("ALTER"):
                raise PermissionError("permission denied for table pipeline_runs")
            return _FakeResult(self.rows)

    conn = _NoDetail([])
    monkeypatch.setattr(pl, "_connect", lambda dsn: conn)
    assert pl._has_detail("dsn") is False
    pl._pg_fetch_runs("dsn", None, None, 10, 0)
    select = next(s for s, _ in conn.statements if s.startswith("SELECT id"))
    assert "stages" not in select


def test_pg_last_runs_is_one_query_for_every_pipeline(pg: _FakeConn) -> None:
    started = datetime(2026, 7, 1, 12, tzinfo=UTC)
    pg.rows = [
        ("run_a", "dbt_build", "success", started, None, 1, None, "schedule", started, None, None),
        ("run_b", "full_ingest", "running", started, None, None, None, "api", started, None, None),
    ]
    out = pl._pg_last_runs("dsn", list(pl.JOB_NAMES))
    assert set(out) == {"dbt_build", "full_ingest"}
    selects = [s for s, _ in pg.statements if s.startswith("SELECT DISTINCT ON")]
    assert len(selects) == 1, "listing pipelines must not be N+1"


def test_pg_trigger_conflicts_then_enqueues(
    client: TestClient, admin: dict[str, str], pg: _FakeConn
) -> None:
    started = datetime(2026, 7, 1, 12, tzinfo=UTC)
    pg.rows = [
        (
            "run_active", "dbt_build", "running", started, None, None, None, "api",
            started, None, None,
        )
    ]
    conflict = client.post("/api/v1/pipelines/dbt_build/run", headers=admin)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["details"]["run_id"] == "run_active"

    pg.rows = []
    ok = client.post("/api/v1/pipelines/dbt_build/run", headers=admin)
    assert ok.status_code == 202
    insert = next(p for s, p in pg.statements if s.startswith("INSERT"))
    assert insert[1] == "dbt_build"
    assert insert[2] == "api"  # so the run reads back as trigger="manual"


def test_pg_unknown_run_is_404(client: TestClient, analyst: dict[str, str], pg: _FakeConn) -> None:
    pg.rows = []
    resp = client.get("/api/v1/pipeline-runs/run_missing", headers=analyst)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_unreachable_run_store_is_503_not_500(
    client: TestClient, analyst: dict[str, str], monkeypatch
) -> None:
    """A refused connection must surface as a clean 503, not an unhandled 500."""

    class _DeadDriver:
        @staticmethod
        def connect(*_: object, **__: object):
            raise OSError("connection refused")

    monkeypatch.setattr(pl, "_pg_dsn", lambda: "postgresql://stub/insight")
    monkeypatch.setattr(pl, "_psycopg", lambda: _DeadDriver)
    resp = client.get("/api/v1/pipeline-runs", headers=analyst)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "dependency_unavailable"
    # The DSN never leaks into the client-facing message.
    assert "postgresql://" not in resp.text
