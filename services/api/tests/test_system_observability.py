"""``/status`` readiness probes, rate limiting (§8), and observability (§9).

All offline: the DuckDB fixture warehouse and the fake provider are real
backends here, and the failure modes are induced by monkeypatching the probe's
collaborators rather than by taking a live service down.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.observability import current_request_id  # noqa: E402
from app.api.routers import system as sysmod  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    return TestClient(create_app())


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


# --- /status -------------------------------------------------------------------


def test_status_reports_real_backend_state(client: TestClient, admin: dict[str, str]) -> None:
    resp = client.get("/status", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["uptime_s"] >= 0

    wh = body["warehouse"]
    assert wh["reachable"] is True
    assert wh["mode"] == "duckdb-fixture"
    assert wh["tables_counted"] == wh["allow_tables"] > 0
    assert wh["row_counts"]["fact_order_items"] > 0
    assert wh["total_rows"] == sum(wh["row_counts"].values())
    assert wh["metrics"] > 0 and wh["dimensions"] > 0

    idx = body["index"]
    assert idx["reachable"] is True
    assert idx["points"] and idx["points"] > 0     # real fixture document count

    llm = body["llm"]
    assert llm["provider"] == "fake"
    assert llm["reachable"] is True
    # Never a credential, under any key.
    assert not any("key" in k.lower() or "secret" in k.lower() for k in llm)

    assert set(body["services"]) == {"postgres", "qdrant", "worker", "llm"}


def test_status_requires_admin(client: TestClient, analyst: dict[str, str]) -> None:
    assert client.get("/status", headers=analyst).status_code == 403
    assert client.get("/status").status_code == 401


def test_status_degrades_instead_of_500(
    client: TestClient, admin: dict[str, str], monkeypatch
) -> None:
    class _DeadWarehouse:
        def run(self, sql: str, params: list):
            raise OSError("connection refused")

    engine = deps.get_engine()
    monkeypatch.setattr(engine, "warehouse", _DeadWarehouse())
    resp = client.get("/status", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["warehouse"]["reachable"] is False
    assert body["services"]["postgres"]["status"] == "down"
    assert "connection refused" in body["services"]["postgres"]["detail"]
    # The rest of the report still works.
    assert body["llm"]["provider"] == "fake"


def test_health_is_public_and_cheap(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- rate limiting (doc 06 §8) --------------------------------------------------


def test_rate_limiting_is_off_by_default_under_tests() -> None:
    assert deps.rate_limiting_enabled() is False


def test_bucket_limits_are_env_configurable(monkeypatch) -> None:
    assert deps.bucket_limit("ask") == (20, 60.0)
    monkeypatch.setenv("RATE_LIMIT_ASK", "5/30")
    assert deps.bucket_limit("ask") == (5, 30.0)
    monkeypatch.setenv("RATE_LIMIT_ASK", "7")
    assert deps.bucket_limit("ask") == (7, 60.0)
    monkeypatch.setenv("RATE_LIMIT_ASK", "nonsense")
    assert deps.bucket_limit("ask") == (20, 60.0)   # a bad override never breaks the API


def test_exhausted_bucket_returns_429_with_retry_after(
    client: TestClient, analyst: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_READ", "1/60")   # analyst multiplier 2 -> 2 requests
    deps.reset_rate_limiter()

    payload = {"metric": "revenue", "dimensions": ["region"], "limit": 3}
    first = client.post("/api/v1/metrics/query", json=payload, headers=analyst)
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"

    assert client.post("/api/v1/metrics/query", json=payload, headers=analyst).status_code == 200

    blocked = client.post("/api/v1/metrics/query", json=payload, headers=analyst)
    assert blocked.status_code == 429
    body = blocked.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["details"]["bucket"] == "read"
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.headers["X-Request-ID"]

    deps.reset_rate_limiter()


def test_buckets_and_users_are_isolated(
    client: TestClient, analyst: dict[str, str], admin: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_READ", "1/60")
    deps.reset_rate_limiter()

    payload = {"metric": "revenue", "dimensions": ["region"], "limit": 3}
    for _ in range(3):
        client.post("/api/v1/metrics/query", json=payload, headers=analyst)
    # Analyst is now blocked; the admin's own budget is untouched...
    assert client.post("/api/v1/metrics/query", json=payload, headers=analyst).status_code == 429
    assert client.post("/api/v1/metrics/query", json=payload, headers=admin).status_code == 200
    # ...and a different bucket is unaffected for the blocked user.
    assert client.get("/api/v1/pipelines", headers=analyst).status_code == 200

    deps.reset_rate_limiter()


def test_token_bucket_refills_over_time() -> None:
    limiter = deps.TokenBucketLimiter()
    assert limiter.check("k", 2, 60.0).allowed
    assert limiter.check("k", 2, 60.0).allowed
    denied = limiter.check("k", 2, 60.0)
    assert not denied.allowed and denied.retry_after >= 1
    # A wide window with a big capacity always admits the first request.
    assert limiter.check("other", 100, 60.0).remaining == 99


# --- observability (doc 06 §9) --------------------------------------------------


def test_request_id_is_echoed_and_propagated(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "req_caller_supplied"})
    assert resp.headers["X-Request-ID"] == "req_caller_supplied"
    # A request without one still gets a minted id.
    assert client.get("/health").headers["X-Request-ID"].startswith("req_")


def test_request_id_is_available_ambiently_and_reset_after(client: TestClient) -> None:
    seen: list[str] = []

    @client.app.get("/_probe_rid")
    def _probe() -> dict[str, str]:
        seen.append(current_request_id())
        return {"ok": "1"}

    client.get("/_probe_rid", headers={"X-Request-ID": "req_ambient"})
    assert seen == ["req_ambient"]
    assert current_request_id() == "-"


def test_access_log_carries_the_request_context(client: TestClient, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="insightgpt.access"):
        client.get("/health", headers={"X-Request-ID": "req_logged"})
    records = [r for r in caplog.records if r.name == "insightgpt.access"]
    assert records, "the middleware must emit one access line per request"
    context = records[-1].context
    assert context["request_id"] == "req_logged"
    assert context["route"] == "/health"
    assert context["status"] == 200
    assert context["latency_ms"] >= 0


def test_authenticated_access_log_records_the_caller(
    client: TestClient, analyst: dict[str, str], caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="insightgpt.access"):
        client.get("/api/v1/metrics", headers=analyst)
    context = [r for r in caplog.records if r.name == "insightgpt.access"][-1].context
    assert context["role"] == "analyst"
    assert context["user_id"]


def test_streamed_answer_logs_its_llm_trace(
    client: TestClient, analyst: dict[str, str], caplog
) -> None:
    """The trace is only complete once the SSE generator has finished."""
    with caplog.at_level(logging.INFO, logger="insightgpt.access"):
        resp = client.post(
            "/api/v1/ask",
            json={"question": "Why did sales decline last quarter?", "stream": True},
            headers=analyst,
        )
        assert resp.status_code == 200
        assert resp.text  # drain the stream
    lines = [
        r.context for r in caplog.records
        if r.name == "insightgpt.access" and r.context["route"] == "/api/v1/ask"
    ]
    assert lines, "the ask request must be logged"
    trace = lines[-1].get("llm_trace")
    assert trace, "a streamed answer must still record its LLM call"
    assert trace[0]["provider"] == "fake"
    assert trace[0]["latency_ms"] >= 0


def test_errors_log_with_the_request_id(client: TestClient, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="insightgpt.error"):
        resp = client.get(
            "/api/v1/reports/rep_missing",
            headers={"X-Request-ID": "req_err", "Authorization": "Bearer nope"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"]["request_id"] == "req_err"
    records = [r for r in caplog.records if r.name == "insightgpt.error"]
    assert records, "a failure must leave a correlatable server-side line"
    assert records[-1].context["request_id"] == "req_err"
    assert records[-1].context["code"] == "unauthorized"


def test_unhandled_exception_is_a_sanitized_500_with_a_log(
    client: TestClient, admin: dict[str, str], monkeypatch, caplog
) -> None:
    def _explode(*_: object, **__: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(sysmod, "_collect", _explode)
    # Let the app's own handler answer rather than re-raising into the test.
    raw = TestClient(client.app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="insightgpt.error"):
        resp = raw.get("/status", headers=admin)
    assert resp.status_code == 500
    body = resp.json()["error"]
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in resp.text  # never leaked to the client
    record = [r for r in caplog.records if r.name == "insightgpt.error"][-1]
    assert record.context["request_id"] == body["request_id"]
    assert "secret internal detail" in record.getMessage()
