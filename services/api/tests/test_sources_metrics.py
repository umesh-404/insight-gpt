"""Governed metrics (§3.2) and data sources (§3.3) — fully offline.

The metric tests run the two shapes the dashboards actually issue (top products
by revenue; inventory at risk by units on hand) against the real catalog +
query builder, on both the DuckDB fixture warehouse and a stubbed Postgres
executor. The source tests exercise the *real* connectivity probes against the
local filesystem and a closed port — no network fixtures required.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_warehouse, reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.routers import sources as src  # noqa: E402
from app.warehouse.executor import QueryResult  # noqa: E402

TOP_PRODUCTS = {
    "metric": "revenue",
    "dimensions": ["product"],
    "filters": [
        {"dimension": "date", "op": "between", "values": ["2026-04-01", "2026-06-30"]}
    ],
    "order_by_metric": "desc",
    "limit": 5,
}
INVENTORY_AT_RISK = {
    "metric": "units_on_hand",
    "dimensions": ["product"],
    "order_by_metric": "asc",
    "limit": 5,
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    src.reset_state()
    return TestClient(create_app())


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> dict[str, str]:
    token = _login(client, "analyst@insightgpt.dev", "analyst-pass")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {_login(client, 'admin@insightgpt.dev', 'admin-pass')}"}


# --- GET /metrics: the full governed catalog -----------------------------------


def test_catalog_exposes_labels_units_and_formats(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.get("/api/v1/metrics", headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    metrics = {m["key"]: m for m in body["metrics"]}
    assert {"revenue", "units_on_hand", "gross_margin_pct", "orders"} <= set(metrics)

    revenue = metrics["revenue"]
    assert revenue["label"] == "Revenue"
    assert revenue["unit"] == "currency"
    assert revenue["format"] == "currency"
    assert revenue["default_agg"] == "sum"
    assert revenue["description"]
    assert "product" in revenue["grain"]

    assert metrics["orders"]["default_agg"] == "count"
    assert metrics["gross_margin_pct"]["default_agg"] == "ratio"
    assert metrics["gross_margin_pct"]["unit"] == "ratio"
    assert metrics["avg_order_value"]["aliases"] == ["aov"]

    dims = {d["key"]: d for d in body["dimensions"]}
    assert dims["date"]["is_date"] is True
    assert dims["date"]["default_grain"] == "month"
    assert dims["product"]["is_date"] is False
    assert dims["product"]["label"] == "Product"

    assert set(body["time_grains"]) >= {"day", "week", "month", "quarter", "year"}
    assert body["limits"]["max_rows"] > 0


# --- POST /metrics/query: the two dashboard shapes -----------------------------


def test_top_products_by_revenue(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post("/api/v1/metrics/query", json=TOP_PRODUCTS, headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert 0 < body["row_count"] <= 5
    names = [c["name"] for c in body["columns"]]
    assert names == ["product", "revenue"]
    assert [c["role"] for c in body["columns"]] == ["dimension", "metric"]

    values = [float(r[1]) for r in body["rows"]]
    assert values == sorted(values, reverse=True)

    assert body["records"][0]["product"] == body["rows"][0][0]
    assert body["meta"]["metric"] == "revenue"
    assert body["meta"]["unit"] == "currency"
    assert body["meta"]["order"] == "desc"
    assert body["meta"]["limit"] == 5

    sql = body["sql"]
    assert "BETWEEN" in sql and "ORDER BY revenue DESC" in sql and "LIMIT 5" in sql


def test_inventory_at_risk(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post("/api/v1/metrics/query", json=INVENTORY_AT_RISK, headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert 0 < body["row_count"] <= 5
    assert [c["name"] for c in body["columns"]] == ["product", "units_on_hand"]
    values = [float(r[1]) for r in body["rows"]]
    assert values == sorted(values)
    assert body["meta"]["order"] == "asc"
    assert body["meta"]["unit"] == "count"


def test_time_grain_passthrough(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/metrics/query",
        json={
            "metric": "revenue",
            "dimensions": ["date"],
            "time_grain": "quarter",
            "time_range": {"start": "2026-01-01", "end": "2026-06-30"},
        },
        headers=analyst,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["time_grain"] == "quarter"
    assert "quarter_label" in body["sql"]
    assert all(str(r[0]).startswith("2026Q") for r in body["rows"])


def test_ordering_spellings_all_work(client: TestClient, analyst: dict[str, str]) -> None:
    for payload in (
        {"order": "desc"},
        {"order_by": "-revenue"},
        {"order_by": "revenue desc"},
        {"order_by_metric": "desc"},
    ):
        resp = client.post(
            "/api/v1/metrics/query",
            json={"metric": "revenue", "dimensions": ["region"], "limit": 3, **payload},
            headers=analyst,
        )
        assert resp.status_code == 200, (payload, resp.text)
        assert resp.json()["meta"]["order"] == "desc", payload


def test_mapping_filters_still_supported(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/metrics/query",
        json={"metric": "revenue", "dimensions": ["region"], "filters": {"region": "North"}},
        headers=analyst,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r[0] for r in body["rows"]] == ["North"]


@pytest.mark.parametrize(
    "payload",
    [
        {"metric": "not_a_metric"},
        {"metric": "revenue", "dimensions": ["not_a_dimension"]},
        {"metric": "revenue", "dimensions": ["store"]},          # not allowed for revenue
        {
            "metric": "revenue",
            "dimensions": ["date"],
            "time_grain": "fortnight",                            # not a governed grain
            "time_range": {"start": "2026-01-01", "end": "2026-06-30"},
        },
        {
            "metric": "revenue",
            "filters": [{"dimension": "product", "op": "between", "values": ["a", "b"]}],
        },
    ],
)
def test_guardrail_failures_are_clean_400s(
    client: TestClient, analyst: dict[str, str], payload: dict
) -> None:
    resp = client.post("/api/v1/metrics/query", json=payload, headers=analyst)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "bad_request"
    assert resp.json()["error"]["message"]


def test_empty_filter_values_are_rejected(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/metrics/query",
        json={
            "metric": "revenue",
            "filters": [{"dimension": "region", "op": "in", "values": []}],
        },
        headers=analyst,
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/v1/metrics/query",
        json={"metric": "revenue", "filters": {"region": []}},
        headers=analyst,
    )
    assert resp.status_code == 400


def test_limit_bounds_are_validated(client: TestClient, analyst: dict[str, str]) -> None:
    assert (
        client.post(
            "/api/v1/metrics/query", json={"metric": "revenue", "limit": 0}, headers=analyst
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/metrics/query", json={"metric": "revenue", "limit": 99999}, headers=analyst
        ).status_code
        == 422
    )


# --- the same requests against a non-DuckDB (Postgres-shaped) executor ---------


class _StubPostgres:
    """Stands in for ``PostgresWarehouse``: records SQL, returns Decimal-ish rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    def run(self, sql: str, params: list) -> QueryResult:
        self.calls.append((sql, list(params)))
        return QueryResult(
            columns=["product", "revenue"],
            rows=[["Widget A", 9100.5], ["Widget B", 4200.0]],
        )


class _DeadPostgres:
    def run(self, sql: str, params: list) -> QueryResult:
        raise OSError("connection to server at \"db\" failed: Connection refused")


def test_query_works_on_the_postgres_path(client: TestClient, analyst: dict[str, str]) -> None:
    stub = _StubPostgres()
    client.app.dependency_overrides[get_warehouse] = lambda: stub
    try:
        for payload in (TOP_PRODUCTS, INVENTORY_AT_RISK):
            resp = client.post("/api/v1/metrics/query", json=payload, headers=analyst)
            assert resp.status_code == 200, resp.text
            assert resp.json()["row_count"] == 2
    finally:
        client.app.dependency_overrides.pop(get_warehouse, None)

    # The builder emits qmark placeholders + the ordered params the executor needs.
    top_sql, top_params = stub.calls[0]
    assert "BETWEEN ? AND ?" in top_sql
    assert top_params == ["2026-04-01", "2026-06-30"]
    assert "ORDER BY units_on_hand ASC" in stub.calls[1][0]


def test_unreachable_warehouse_is_503_not_500(client: TestClient, analyst: dict[str, str]) -> None:
    client.app.dependency_overrides[get_warehouse] = _DeadPostgres
    try:
        resp = client.post("/api/v1/metrics/query", json=TOP_PRODUCTS, headers=analyst)
    finally:
        client.app.dependency_overrides.pop(get_warehouse, None)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "dependency_unavailable"


def test_warehouse_rejection_is_400_not_500(client: TestClient, analyst: dict[str, str]) -> None:
    class _Picky:
        def run(self, sql: str, params: list) -> QueryResult:
            raise ValueError("column does not exist")

    client.app.dependency_overrides[get_warehouse] = _Picky
    try:
        resp = client.post("/api/v1/metrics/query", json=TOP_PRODUCTS, headers=analyst)
    finally:
        client.app.dependency_overrides.pop(get_warehouse, None)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


# --- sources ------------------------------------------------------------------


def _create(client: TestClient, admin: dict[str, str], **body) -> dict:
    resp = client.post("/api/v1/sources", json=body, headers=admin)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_source_config_is_validated(client: TestClient, admin: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/sources", json={"name": "no dsn", "kind": "postgres"}, headers=admin
    )
    assert resp.status_code == 400
    assert "dsn" in resp.json()["error"]["message"]

    resp = client.post(
        "/api/v1/sources", json={"name": "no path", "kind": "documents"}, headers=admin
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["details"]["expected_option"] == "path"


def test_documents_source_test_reads_the_real_folder(
    client: TestClient, admin: dict[str, str], tmp_path
) -> None:
    for i in range(3):
        (tmp_path / f"doc{i}.md").write_text("hello", encoding="utf-8")
    source = _create(
        client, admin, name="tickets", kind="documents", options={"path": str(tmp_path)}
    )
    assert source["status"] == "untested"

    resp = client.post(f"/api/v1/sources/{source['id']}/test", headers=admin)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["ok"] is True
    assert result["tables_seen"] == 3
    assert result["checked"] == "filesystem"
    assert result["error_code"] is None
    assert result["latency_ms"] >= 0

    after = client.get(f"/api/v1/sources/{source['id']}", headers=admin).json()
    assert after["status"] == "ok"
    assert after["last_tested_at"] is not None
    assert "dsn" not in after and "options" not in after


def test_csv_source_test_reports_a_missing_path(
    client: TestClient, admin: dict[str, str], tmp_path
) -> None:
    missing = tmp_path / "nowhere" / "data.csv"
    source = _create(client, admin, name="csv", kind="csv", options={"path": str(missing)})
    result = client.post(f"/api/v1/sources/{source['id']}/test", headers=admin).json()
    assert result["ok"] is False
    assert result["error_code"] == "not_found"
    assert result["tables_seen"] == 0

    after = client.get(f"/api/v1/sources/{source['id']}", headers=admin).json()
    assert after["status"] == "error"


def test_postgres_source_test_really_connects_and_hides_the_password(
    client: TestClient, admin: dict[str, str]
) -> None:
    dsn = "postgresql://reporter:sup3r-s3cret@127.0.0.1:1/insight"
    source = _create(
        client, admin, name="warehouse", kind="postgres", dsn=dsn,
        options={"timeout_s": 2},
    )
    resp = client.post(f"/api/v1/sources/{source['id']}/test", headers=admin)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["ok"] is False
    assert result["error_code"] == "connect_failed"
    assert result["checked"] == "connect"
    # The message describes the failure without ever echoing the credential.
    assert "sup3r-s3cret" not in resp.text
    assert "reporter" not in resp.text
    assert dsn not in resp.text


def test_mysql_source_test_is_an_honest_tcp_probe(
    client: TestClient, admin: dict[str, str]
) -> None:
    source = _create(
        client, admin, name="legacy", kind="mysql",
        dsn="mysql://root:hunter2@127.0.0.1:1/shop", options={"timeout_s": 2},
    )
    result = client.post(f"/api/v1/sources/{source['id']}/test", headers=admin).json()
    assert result["ok"] is False
    assert result["checked"] == "tcp"
    assert "hunter2" not in str(result)


def test_source_lifecycle_and_404s(client: TestClient, admin: dict[str, str], tmp_path) -> None:
    source = _create(
        client, admin, name="gone", kind="documents", options={"path": str(tmp_path)}
    )
    listed = client.get("/api/v1/sources", headers=admin).json()
    assert any(s["id"] == source["id"] for s in listed)

    assert client.delete(f"/api/v1/sources/{source['id']}", headers=admin).status_code == 200
    # Soft-deleted: invisible to every read, and no longer testable.
    for resp in (
        client.get(f"/api/v1/sources/{source['id']}", headers=admin),
        client.post(f"/api/v1/sources/{source['id']}/test", headers=admin),
        client.delete(f"/api/v1/sources/{source['id']}", headers=admin),
    ):
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/sources/src_nope", headers=admin).status_code == 404


def test_sources_are_admin_only(client: TestClient, analyst: dict[str, str]) -> None:
    assert client.get("/api/v1/sources", headers=analyst).status_code == 403


def test_secret_scrubbing_covers_url_and_keyword_dsns() -> None:
    url = "postgresql://bob:p%40ss word@host:5432/db"
    parts = src._secret_parts(url)
    assert any("p%40ss" in p for p in parts)
    kv = "host=db user=bob password=hunter2 dbname=insight"
    assert "hunter2" in src._secret_parts(kv)
    scrubbed = src._scrub("FATAL: password authentication failed for hunter2", ["hunter2"])
    assert "hunter2" not in scrubbed


def test_host_port_parsing_handles_both_dsn_forms() -> None:
    url = "postgresql://u:p@db.internal:6543/x"
    assert src._host_port(url, "postgres") == ("db.internal", 6543)
    assert src._host_port("postgresql://u:p@db.internal/x", "postgres") == ("db.internal", 5432)
    assert src._host_port("host=db.internal port=3307", "mysql") == ("db.internal", 3307)
    assert src._host_port("not-a-dsn", "mysql") is None
