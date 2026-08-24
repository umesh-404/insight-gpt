"""Chunking: whole-record vs heading-aware, breadcrumb header, content purity."""

from __future__ import annotations

from retrieval.chunking import chunk_document, embed_text
from retrieval.config import ChunkingConfig
from retrieval.models import Document

CFG = ChunkingConfig(
    max_chunk_chars=400, min_chunk_chars=40, overlap_lines=2, contextual_header=True
)


def _doc(**kw) -> Document:
    base = dict(doc_id="D1", source_type="ticket", title="T", body="B")
    base.update(kw)
    return Document.from_dict(base)


def test_ticket_is_one_whole_chunk():
    doc = _doc(source_type="ticket", body="Short complaint about a late delivery.")
    chunks = chunk_document(doc, CFG)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].chunk_total == 1
    assert chunks[0].heading_path is None


def test_review_is_one_whole_chunk():
    chunks = chunk_document(_doc(source_type="review", body="Loved it. Fast shipping."), CFG)
    assert len(chunks) == 1


def test_report_splits_on_headings_with_breadcrumb_trail():
    body = (
        "# Q2 Review\n\nIntro preamble text goes here to exceed the min size floor.\n\n"
        "## Fulfilment\n\n### Root cause\n\n"
        "The North fulfilment centre backlog was the dominant issue this quarter, "
        "concentrated in electronics and driving refunds and cancellations widely.\n\n"
        "### Remediation\n\n"
        "Temporary capacity was added and orders rerouted through the West centre "
        "so the backlog could clear before the end of the reporting period.\n"
    )
    doc = _doc(source_type="report", title="Q2 Review", body=body)
    chunks = chunk_document(doc, CFG)
    assert len(chunks) >= 3
    paths = [c.heading_path for c in chunks if c.heading_path]
    assert any("Fulfilment > Root cause" in p for p in paths)
    assert any("Remediation" in p for p in paths)
    # chunk_index / chunk_total are consistent across the set.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.chunk_total == len(chunks) for c in chunks)


def test_breadcrumb_header_prefixes_embedded_text_only():
    doc = _doc(source_type="report", title="Q2 Review", date="2026-05-14", author_role="manager",
               body="## Root cause\n\nThe fulfilment backlog delayed shipments materially.")
    chunk = chunk_document(doc, CFG)[0]
    embedded = embed_text(doc, chunk, CFG)
    # Header carries title, section path, date, source type, role...
    assert "Q2 Review" in embedded
    assert "2026-05-14" in embedded
    assert "report" in embedded
    # ...but the stored content never carries the header metadata line.
    assert "2026-05-14" not in chunk.content
    assert "report · manager" not in chunk.content
    assert "The fulfilment backlog delayed shipments materially." in chunk.content


def test_header_disabled_returns_content_verbatim():
    off = ChunkingConfig(max_chunk_chars=400, min_chunk_chars=40, overlap_lines=2,
                         contextual_header=False)
    doc = _doc(body="Plain body.")
    chunk = chunk_document(doc, off)[0]
    assert embed_text(doc, chunk, off) == chunk.content


def test_pathologically_long_record_is_windowed():
    long_body = "\n".join(f"log line {i} with some detail padding here" for i in range(200))
    doc = _doc(source_type="ticket", body=long_body)
    chunks = chunk_document(doc, CFG)
    assert len(chunks) > 1
    assert all(len(c.content) <= CFG.max_chunk_chars + 200 for c in chunks)


def test_report_without_headings_still_yields_a_chunk():
    doc = _doc(source_type="report", body="A flat report body with no markdown headings at all.")
    chunks = chunk_document(doc, CFG)
    assert len(chunks) == 1
    assert chunks[0].content
