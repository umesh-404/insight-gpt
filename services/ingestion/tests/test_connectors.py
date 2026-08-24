"""Offline tests for the connectors and the loader's graceful degradation."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.connectors.csv_connector import CSVConnector
from ingestion.connectors.document_connector import DocumentConnector
from ingestion.loader import RawLoader


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_csv_connector_discovers_and_extracts(tmp_path: Path) -> None:
    _write(tmp_path / "orders.csv", "order_id,status\n1,placed\n2,shipped\n")
    conn = CSVConnector("retail_csv", tmp_path)
    units = conn.discover()
    assert [u.unit_id for u in units] == ["orders"]
    assert len(units[0].fingerprint) == 64  # sha256 hex
    rows = list(conn.extract(units[0]))
    assert rows == [
        {"order_id": "1", "status": "placed"},
        {"order_id": "2", "status": "shipped"},
    ]


def test_csv_fingerprint_changes_with_content(tmp_path: Path) -> None:
    p = tmp_path / "products.csv"
    _write(p, "product_id\n1\n")
    conn = CSVConnector("retail_csv", tmp_path)
    first = conn.discover()[0].fingerprint
    _write(p, "product_id\n1\n2\n")
    second = conn.discover()[0].fingerprint
    assert first != second


def test_document_connector_reads_json_array(tmp_path: Path) -> None:
    docs = [
        {"doc_id": "TICKET-1", "title": "Late delivery", "body": "arrived late",
         "region": "North", "doc_type": "ticket"},
    ]
    _write(tmp_path / "support_tickets.json", json.dumps(docs))
    conn = DocumentConnector("retail_docs", tmp_path)
    units = conn.discover()
    extracted = list(conn.extract(units[0]))
    assert len(extracted) == 1
    assert extracted[0].doc_id == "TICKET-1"
    assert "Late delivery" in extracted[0].text
    assert extracted[0].metadata["region"] == "North"


def test_document_connector_skips_credentials_and_binary(tmp_path: Path) -> None:
    _write(tmp_path / "notes.txt", "safe content")
    _write(tmp_path / ".env", "SECRET=abc")
    _write(tmp_path / "server.pem", "-----BEGIN PRIVATE KEY-----")
    _write(tmp_path / "empty.txt", "")
    (tmp_path / "blob.txt").write_bytes(b"bad\x00binary")
    conn = DocumentConnector("retail_docs", tmp_path)
    unit_ids = {u.unit_id for u in conn.discover()}
    assert "notes.txt" in unit_ids
    assert ".env" not in unit_ids
    assert "server.pem" not in unit_ids
    assert "empty.txt" not in unit_ids
    assert "blob.txt" not in unit_ids
    assert any("credential" in r for r in conn.skipped.values())


def test_loader_degrades_without_postgres() -> None:
    loader = RawLoader(dsn=None)
    assert loader.available is False
    result = loader.load_records(
        source="retail_csv",
        unit_id="orders",
        columns=["order_id"],
        rows=[{"order_id": "1"}],
        fingerprint="deadbeef",
        batch_id="b1",
    )
    assert result.status == "skipped_unavailable"
    assert "no POSTGRES_DSN" in result.message
