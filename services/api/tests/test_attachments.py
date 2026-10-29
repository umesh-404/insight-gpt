"""Tests for attachment parsing and file-grounded Q&A."""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.deps import reset_caches
from app.api.main import create_app
from app.engine.attachments import (
    AttachmentInput,
    _parse_csv_or_tsv,
    _parse_docx,
    _parse_json,
)
from app.engine.engine import InsightEngine


@pytest.fixture(scope="module")
def app():
    reset_caches()
    return create_app()


@pytest.fixture(scope="module")
def client(app) -> TestClient:
    return TestClient(app)


def _login(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@insightgpt.dev", "password": "analyst-pass"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_parse_csv_creates_table_and_summary():
    csv_data = "Region,Product,Revenue,Units\nNorth,Widget A,1500,25\nSouth,Widget B,3200,40\n"
    proc = _parse_csv_or_tsv("sales.csv", csv_data)
    assert proc.name == "sales.csv"
    assert proc.row_count == 2
    assert proc.column_names == ["Region", "Product", "Revenue", "Units"]
    assert proc.table is not None
    assert len(proc.table.rows) == 2
    assert "Revenue" in proc.stats
    assert proc.stats["Revenue"]["sum"] == 4700.0


def test_parse_json_array_creates_table():
    json_data = json.dumps([
        {"id": 1, "customer": "Acme", "spend": 450.5},
        {"id": 2, "customer": "Beta", "spend": 980.0},
    ])
    proc = _parse_json("customers.json", json_data)
    assert proc.row_count == 2
    assert proc.table is not None
    assert proc.column_names == ["id", "customer", "spend"]


def test_parse_docx_extracts_text():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Quarterly Strategy Review: '
            'Q3 targets exceeded by 18%.</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        z.writestr("word/document.xml", doc_xml)
    proc = _parse_docx("strategy.docx", buf.getvalue())
    assert "Quarterly Strategy Review" in proc.text_content
    assert "18%" in proc.text_content


def test_engine_ask_with_attached_csv():
    engine = InsightEngine.fixture()
    csv_data = "Month,Sales,Target\nJuly,50000,45000\nAugust,62000,50000\n"
    att = AttachmentInput(name="report.csv", content=csv_data, content_type="text/csv")
    env = engine.ask("What are the key insights in this report?", attachments=[att])

    assert env.route in ("unstructured", "hybrid")
    assert not env.abstained
    assert any("report.csv" in c.title for c in env.citations)
    assert len(env.tables) >= 1
    assert "report.csv" in env.tables[0].title or "Attached" in env.tables[0].title


def test_api_ask_with_attachments_json(client: TestClient):
    token = _login(client)
    csv_text = "Item,Price,Qty\nKeyboard,75,10\nMonitor,300,5\n"
    resp = client.post(
        "/api/v1/ask",
        json={
            "question": "What items are listed in the attached file?",
            "stream": False,
            "attachments": [
                {"name": "items.csv", "content": csv_text, "content_type": "text/csv"}
            ],
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert not data["abstained"]
    assert any("items.csv" in c["title"] for c in data["citations"])


def test_api_ask_upload_multipart(client: TestClient):
    token = _login(client)
    file_bytes = b"Department,Budget,Spend\nEngineering,100000,85000\nMarketing,50000,52000\n"
    files = [("files", ("budget.csv", file_bytes, "text/csv"))]
    data = {"question": "Summarize the department budgets in this file."}
    resp = client.post(
        "/api/v1/ask/upload",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    res = resp.json()
    assert not res["abstained"]
    assert any("budget.csv" in c["title"] for c in res["citations"])
