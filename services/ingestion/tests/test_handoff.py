"""The document hand-off: ingestion publishes a corpus retrieval can index.

Before this wiring existed, the document branch redacted documents and threw
them away, and the reindex job re-embedded six built-in demo documents while
reporting success. These tests pin the contract that replaced it:

* every generated document is published, redacted, with its filter metadata;
* the file is a deterministic function of its contents (byte-identical re-runs);
* an unchanged corpus is not rewritten at all;
* the path published to is exactly the one the worker's reindex job reads.
"""

from __future__ import annotations

import json

import pytest

from ingestion import corpus
from ingestion.config import IngestionSettings
from ingestion.run import _to_corpus_record, run

GENERATED_DOCS = [
    {
        "doc_id": "TICKET-40122",
        "doc_type": "ticket",
        "title": "Late delivery - North electronics",
        "body": "Backlog at the North centre. Reachable at ada@example.com.",
        "created_ts": "2026-05-08T09:00:00",
        "region": "North",
        "category": "Electronics",
        "product_id": 1,
        "product_sku": "ELE-LAP-0001",
        "author_role": "support_agent",
    },
    {
        "doc_id": "REVIEW-90001",
        "doc_type": "review",
        "title": "Great",
        "body": "Fast shipping in the South.",
        "created_ts": "2026-05-11T12:00:00",
        "region": "South",
        "category": "Apparel",
        "author_role": "customer",
    },
]


@pytest.fixture
def workspace(tmp_path):
    """A generator-shaped data directory plus somewhere to publish to."""
    docs_dir = tmp_path / "generated" / "documents"
    docs_dir.mkdir(parents=True)
    (docs_dir / "support_tickets.json").write_text(
        json.dumps([GENERATED_DOCS[0]]), encoding="utf-8"
    )
    (docs_dir / "reviews.json").write_text(
        json.dumps([GENERATED_DOCS[1]]), encoding="utf-8"
    )
    return IngestionSettings(
        postgres_dsn=None,
        generated_dir=tmp_path / "generated",
        document_corpus_path=tmp_path / "ingested" / "documents.json",
    )


# --- the run publishes ---------------------------------------------------------
def test_document_run_publishes_the_corpus(workspace):
    stats = run("full_ingest", "documents", workspace)

    assert stats.documents_published == 2
    assert stats.documents_changed == 2
    assert workspace.document_corpus_path.exists()
    published = json.loads(workspace.document_corpus_path.read_text(encoding="utf-8"))
    assert [d["doc_id"] for d in published] == ["REVIEW-90001", "TICKET-40122"]


def test_published_documents_are_redacted(workspace):
    run("full_ingest", "documents", workspace)
    blob = workspace.document_corpus_path.read_text(encoding="utf-8")
    # The address was in the ticket body; redaction runs before publication, so
    # it can never reach an embedder or the vector store.
    assert "ada@example.com" not in blob
    assert "[REDACTED]" in blob


def test_published_documents_keep_their_filter_metadata(workspace):
    run("full_ingest", "documents", workspace)
    published = json.loads(workspace.document_corpus_path.read_text(encoding="utf-8"))
    ticket = next(d for d in published if d["doc_id"] == "TICKET-40122")
    assert ticket["region"] == "North"
    assert ticket["category"] == "Electronics"
    assert ticket["doc_type"] == "ticket"
    assert ticket["created_ts"] == "2026-05-08T09:00:00"
    assert ticket["product_sku"] == "ELE-LAP-0001"


def test_title_and_body_are_published_separately(workspace):
    """The connector joins title + body into one text; publication must split it
    back, or every document would be indexed with an empty title and the
    chunker's breadcrumb header would lose its best vocabulary."""
    run("full_ingest", "documents", workspace)
    published = json.loads(workspace.document_corpus_path.read_text(encoding="utf-8"))
    ticket = next(d for d in published if d["doc_id"] == "TICKET-40122")
    assert ticket["title"] == "Late delivery - North electronics"
    assert ticket["title"] not in ticket["body"]


def test_every_document_carries_a_content_hash(workspace):
    run("full_ingest", "documents", workspace)
    published = json.loads(workspace.document_corpus_path.read_text(encoding="utf-8"))
    assert all(d[corpus.CONTENT_HASH_KEY] for d in published)


# --- idempotency ---------------------------------------------------------------
def test_re_running_leaves_the_file_byte_identical(workspace):
    run("full_ingest", "documents", workspace)
    first = workspace.document_corpus_path.read_bytes()
    stats = run("full_ingest", "documents", workspace)
    assert workspace.document_corpus_path.read_bytes() == first
    assert stats.documents_changed == 0


def test_an_unchanged_corpus_is_not_rewritten(workspace):
    run("full_ingest", "documents", workspace)
    before = workspace.document_corpus_path.stat().st_mtime_ns
    run("incremental_sync", "documents", workspace)
    assert workspace.document_corpus_path.stat().st_mtime_ns == before


def test_an_edited_document_is_counted_as_changed(workspace, tmp_path):
    run("full_ingest", "documents", workspace)
    edited = dict(GENERATED_DOCS[1], body="Fast shipping in the South. Would buy again.")
    (tmp_path / "generated" / "documents" / "reviews.json").write_text(
        json.dumps([edited]), encoding="utf-8"
    )
    stats = run("incremental_sync", "documents", workspace)
    assert stats.documents_changed == 1


# --- the contract with retrieval ----------------------------------------------
def test_default_corpus_path_is_the_one_retrieval_reads():
    # services/retrieval's `default_corpus_path()` and the worker's
    # `document_corpus_path` both point here. Keep them in step.
    assert IngestionSettings().document_corpus_path.parts[-3:] == (
        "data",
        "ingested",
        "documents.json",
    )


# --- corpus helpers ------------------------------------------------------------
def test_content_hash_ignores_key_order():
    a = {"doc_id": "X", "doc_type": "ticket", "title": "t", "body": "b"}
    b = {"body": "b", "title": "t", "doc_type": "ticket", "doc_id": "X"}
    assert corpus.content_hash(a) == corpus.content_hash(b)


def test_content_hash_treats_aliases_as_the_same_field():
    generated = {"doc_id": "X", "doc_type": "ticket", "title": "t", "body": "b"}
    canonical = {"doc_id": "X", "source_type": "ticket", "title": "t", "body": "b"}
    assert corpus.content_hash(generated) == corpus.content_hash(canonical)


def test_publish_is_atomic_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "out" / "documents.json"
    corpus.publish(target, GENERATED_DOCS)
    assert list(target.parent.iterdir()) == [target]


def test_publish_reports_whether_it_wrote(tmp_path):
    target = tmp_path / "documents.json"
    assert corpus.publish(target, GENERATED_DOCS).written is True
    assert corpus.publish(target, GENERATED_DOCS).written is False


def test_corpus_record_promotes_only_a_real_title():
    """A body whose first paragraph is long prose must not become the title."""
    from ingestion.connectors.base import Document

    long_head = "x" * 250
    doc = Document(doc_id="D", text=f"{long_head}\n\nrest", metadata={})
    record = _to_corpus_record(doc)
    assert record["title"] == ""
    assert record["body"].startswith(long_head)
