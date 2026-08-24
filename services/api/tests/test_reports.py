"""Report generation + export (doc 06 §3.5) — offline, fixture engine.

Covers the Markdown export and the real server-side PDF: a valid document with
the right content type, an attachment filename, and the report's actual content
(title, generated-at, section narratives, key figures, citations).
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import re  # noqa: E402
import zlib  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import reset_caches  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.api.routers import reports as reports_router  # noqa: E402
from app.engine.envelope import Citation, Table  # noqa: E402

PERIOD = {"grain": "month", "start": "2026-04-01", "end": "2026-06-30"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    reset_caches()
    reports_router.reset_state()
    return TestClient(create_app())


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pdf_text_content(pdf: bytes) -> str:
    """Decompress the page content streams and pull out the drawn text runs."""
    out: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        try:
            content = zlib.decompress(match.group(1))
        except zlib.error:
            content = match.group(1)
        for run in re.findall(rb"\((.*?)\) Tj", content, re.S):
            text = run.decode("latin-1")
            # PDF literal strings escape ( ) and \ — undo that for assertions.
            out.append(
                text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
            )
    return "\n".join(out)


@pytest.fixture(scope="module")
def analyst(client: TestClient) -> dict[str, str]:
    return _auth(_login(client, "analyst@insightgpt.dev", "analyst-pass"))


@pytest.fixture(scope="module")
def report_id(client: TestClient, analyst: dict[str, str]) -> str:
    resp = client.post(
        "/api/v1/reports",
        json={
            "title": "Q2 executive review",
            "period": PERIOD,
            "sections": ["kpis", "sales", "voice_of_customer"],
        },
        headers=analyst,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "ready", body
    assert body["href"].endswith(body["report_id"])
    return body["report_id"]


def test_report_is_stored_with_blocks(
    client: TestClient, analyst: dict[str, str], report_id: str
) -> None:
    resp = client.get(f"/api/v1/reports/{report_id}", headers=analyst)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["title"] == "Q2 executive review"
    assert len(body["blocks"]) == 3
    headings = [b["heading"] for b in body["blocks"]]
    assert headings == ["Key metrics", "Sales analysis", "Voice of customer"]
    assert all(b["prose"] for b in body["blocks"])
    # Key figures travel with the block, not just the narrative.
    assert any(b["tables"] for b in body["blocks"])
    assert body["error"] is None


def test_unknown_report_is_404(client: TestClient, analyst: dict[str, str]) -> None:
    resp = client.get("/api/v1/reports/rep_missing", headers=analyst)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_export_markdown(client: TestClient, analyst: dict[str, str], report_id: str) -> None:
    resp = client.get(f"/api/v1/reports/{report_id}/export", headers=analyst)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert f'filename="{report_id}.md"' in resp.headers["content-disposition"]
    text = resp.text
    assert text.startswith("# Q2 executive review")
    assert "_Generated:" in text
    assert "## Key metrics" in text


def test_export_pdf_is_a_real_document(
    client: TestClient, analyst: dict[str, str], report_id: str
) -> None:
    resp = client.get(
        f"/api/v1/reports/{report_id}/export", params={"format": "pdf"}, headers=analyst
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert f'filename="{report_id}.pdf"' in resp.headers["content-disposition"]
    body = resp.content
    assert body.startswith(b"%PDF-")
    assert body.rstrip().endswith(b"%%EOF")
    # A placeholder page would be a few hundred bytes; real content is not.
    assert len(body) > 2000, f"PDF suspiciously small: {len(body)} bytes"
    assert int(resp.headers["content-length"]) == len(body)

    # ...and it is the *report's* content, not a placeholder page.
    text = _pdf_text_content(body)
    assert "Q2 executive review" in text
    assert "Generated:" in text
    assert "2026-04-01 to 2026-06-30" in text
    for heading in ("Key metrics", "Sales analysis", "Voice of customer"):
        assert heading in text
    assert "Sources" in text


def test_export_rejects_unknown_format(
    client: TestClient, analyst: dict[str, str], report_id: str
) -> None:
    resp = client.get(
        f"/api/v1/reports/{report_id}/export", params={"format": "docx"}, headers=analyst
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_viewer_cannot_create_reports(client: TestClient) -> None:
    token = _login(client, "viewer@insightgpt.dev", "viewer-pass")
    resp = client.post(
        "/api/v1/reports",
        json={"title": "nope", "period": PERIOD, "sections": ["kpis"]},
        headers=_auth(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_reports_require_at_least_one_section(
    client: TestClient, analyst: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/reports",
        json={"title": "empty", "period": PERIOD, "sections": []},
        headers=analyst,
    )
    assert resp.status_code == 422


def test_list_reports_is_newest_first(
    client: TestClient, analyst: dict[str, str], report_id: str
) -> None:
    resp = client.get("/api/v1/reports", headers=analyst)
    assert resp.status_code == 200
    items = resp.json()
    assert any(r["id"] == report_id for r in items)
    stamps = [r["created_at"] for r in items]
    assert stamps == sorted(stamps, reverse=True)


# --- direct renderer unit tests ------------------------------------------------


def _sample_report(status: str = "ready") -> reports_router.Report:
    return reports_router.Report(
        id="rep_unit",
        status=status,  # type: ignore[arg-type]
        title="Unicode — “quarter” review",
        period=reports_router.TimeRange(grain="month", start="2026-04-01", end="2026-06-30"),
        created_at=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
        error="boom" if status == "failed" else None,
        blocks=[
            reports_router.ReportBlock(
                heading="Sales analysis",
                prose="Revenue fell 12% — driven by the North region…",
                tables=[
                    Table(
                        title="Revenue by region",
                        columns=["region", "revenue"],
                        rows=[["North", 1234.5], ["South", None], ["East", 42]] * 6,
                    )
                ],
                citations=[
                    Citation(
                        n=1, doc_id="d1", source_type="ticket",
                        title="Delays", date="2026-05-01",
                    )
                ],
            )
        ],
    )


def test_render_pdf_handles_unicode_and_tables() -> None:
    pdf = reports_router.render_pdf(_sample_report())
    assert pdf.startswith(b"%PDF-")
    text = _pdf_text_content(pdf)
    # Smart punctuation is transliterated, not dropped.
    assert 'Unicode - "quarter" review' in text
    assert "Revenue fell 12% - driven by the North region..." in text
    assert "Revenue by region" in text
    assert "1,234.50" in text
    assert "more row(s) omitted" in text  # the table is truncated, not endless


def test_render_pdf_of_failed_report_states_the_reason() -> None:
    pdf = reports_router.render_pdf(_sample_report("failed"))
    assert pdf.startswith(b"%PDF-")


def test_markdown_renders_tables_and_sources() -> None:
    text = reports_router._to_markdown(_sample_report())
    assert "| region | revenue |" in text
    assert "| North | 1,234.50 |" in text
    assert "| South | - |" in text          # NULL cells do not blow up
    assert "**Sources**" in text
    assert "[1] Delays (2026-05-01)" in text
