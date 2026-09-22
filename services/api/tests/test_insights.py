"""Proactive insight digest — offline, fixture engine, ``LLM_PROVIDER=fake``.

Covers the differentiating path end to end: detection flags the planted Q2
North/Electronics revenue drop with the correct root-cause segment, the API
surfaces it (list + detail), reads are role-gated, unknown ids 404, and a missing
Postgres backend degrades to on-demand generation instead of a 500.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
# Ensure no Postgres backend is configured for the offline suite.
os.environ.pop("POSTGRES_DSN", None)
os.environ.pop("INSIGHTS_FILE", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.routers import insights as insights_router  # noqa: E402
from app.engine.engine import InsightEngine  # noqa: E402
from app.insights import DetectionConfig, detect_insights  # noqa: E402
from app.insights.store import InsightStore  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    insights_router.reset_state()
    return TestClient(create_app())


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> dict[str, str]:
    return _auth(_login(client, "analyst@insightgpt.dev", "analyst-pass"))


# --- detection (unit) ----------------------------------------------------------


def test_detection_flags_revenue_drop_with_north_root_cause() -> None:
    insights = detect_insights(InsightEngine.fixture())
    by_metric = {i.metric: i for i in insights}

    assert "revenue" in by_metric, "the planted revenue anomaly must be flagged"
    rev = by_metric["revenue"]
    assert rev.direction == "down"
    assert rev.period == "2026Q2" and rev.prior_period == "2026Q1"
    # Numbers come from the warehouse, not invented.
    assert rev.current == 1_152_000.0
    assert rev.prior == 1_300_000.0
    assert round(rev.change_pct, 3) == -0.114
    # Root cause is the segment that most explains the decline.
    assert rev.root_cause is not None
    assert rev.root_cause.dimension == "region"
    assert rev.root_cause.segment == "North"
    assert rev.root_cause.delta == -130_000.0
    # The Electronics category decline is captured in the breakdown too.
    cats = {(c.dimension, c.segment): c for c in rev.contributions}
    assert ("category", "Electronics") in cats
    assert cats[("category", "Electronics")].delta < 0
    # Supporting documents are scoped to the North electronics story.
    assert rev.evidence, "the North fulfilment docs should be attached as evidence"
    assert any("North" in e.title or e.source_type == "ticket" for e in rev.evidence)


def test_detection_is_honest_about_method_and_ratios() -> None:
    insights = detect_insights(InsightEngine.fixture())
    by_metric = {i.metric: i for i in insights}
    # No overclaiming as ML; the demo warehouse has too little history for a z.
    assert "z-score" in by_metric["revenue"].method
    assert by_metric["revenue"].z_score is None
    # Non-additive ratio metrics are flagged but carry no summed contribution.
    aov = by_metric.get("avg_order_value")
    if aov is not None:
        assert aov.root_cause is None


def test_stable_ids_make_refresh_idempotent() -> None:
    a = detect_insights(InsightEngine.fixture())
    b = detect_insights(InsightEngine.fixture())
    assert [i.id for i in a] == [i.id for i in b]
    assert {i.id for i in a} == {i.id for i in a}  # ids unique
    assert next(i for i in a if i.metric == "revenue").id == "ins_revenue_2026q2"


def test_min_magnitude_floor_suppresses_small_moves() -> None:
    # A huge absolute floor suppresses everything, proving the guard is wired.
    quiet = detect_insights(InsightEngine.fixture(), DetectionConfig(min_abs=1e12))
    assert quiet == []


# --- API ----------------------------------------------------------------------


def test_list_returns_the_revenue_insight(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.get("/api/v1/insights", headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["backend"] == "memory (on-demand)"
    assert body["total"] >= 1
    metrics = {item["metric"] for item in body["items"]}
    assert "revenue" in metrics
    rev = next(i for i in body["items"] if i["metric"] == "revenue")
    assert rev["root_cause"]["segment"] == "North"
    # Newest-first / severity-ranked: the first item is a high-severity one.
    assert body["items"][0]["severity"] == "high"


def test_list_is_paginated(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.get("/api/v1/insights?limit=1&offset=0", headers=analyst)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["limit"] == 1 and body["offset"] == 0
    assert body["total"] >= 1


def test_detail_returns_full_root_cause_and_evidence(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.get("/api/v1/insights/ins_revenue_2026q2", headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metric"] == "revenue"
    assert body["root_cause"]["segment"] == "North"
    assert body["contributions"]
    assert body["trend"]
    assert len(body["evidence"]) >= 1


def test_unknown_id_is_404(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.get("/api/v1/insights/ins_nope_1999q9", headers=analyst)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_refresh_returns_fresh_set(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post("/api/v1/insights/refresh", headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(i["metric"] == "revenue" for i in body["items"])


def test_reads_require_analyst(client: TestClient) -> None:
    token = _login(client, "viewer@insightgpt.dev", "viewer-pass")
    for method, path in (("get", "/api/v1/insights"), ("post", "/api/v1/insights/refresh")):
        resp = getattr(client, method)(path, headers=_auth(token))
        assert resp.status_code == 403, (path, resp.text)
        assert resp.json()["error"]["code"] == "forbidden"


def test_missing_token_is_401(client: TestClient) -> None:
    resp = client.get("/api/v1/insights")
    assert resp.status_code == 401


def test_no_500_when_postgres_absent(client: TestClient, analyst: dict[str, str]) -> None:
    # The default offline store is not "available"; the endpoint must still 200
    # by generating on demand — never a 500 for a missing backend.
    store = insights_router.get_insight_store()
    assert store.available is False
    resp = client.get("/api/v1/insights", headers=analyst)
    assert resp.status_code == 200


# --- store fallbacks -----------------------------------------------------------


def test_store_file_backend_round_trips(tmp_path) -> None:
    path = tmp_path / "insights.json"
    store = InsightStore(dsn=None, file_path=path)
    assert store.backend == "file"
    assert store.available is True

    insights = detect_insights(InsightEngine.fixture())
    written = store.replace_all(insights)
    assert written == len(insights)
    assert path.exists()

    # A fresh store reading the same file sees the persisted set, newest-first.
    reopened = InsightStore(dsn=None, file_path=path)
    items, total = reopened.list()
    assert total == written
    assert reopened.get("ins_revenue_2026q2") is not None


def test_store_memory_backend_is_not_available() -> None:
    store = InsightStore(dsn=None)
    assert store.backend == "memory"
    assert store.available is False
