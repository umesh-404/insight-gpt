"""The canonical document schema: every producer lands on the same shape.

These are the assertions that make the ingestion -> retrieval hand-off real. A
generated document uses ``doc_type`` / ``created_ts`` / ``author_role:
support_agent``; the built-in samples use ``source_type`` / ``date`` /
``author_role: agent``. If those did not converge, the engine's ``region`` /
``category`` / date filters would match nothing and retrieval would fail
silently — returning zero documents rather than an error.
"""

from __future__ import annotations

import pytest

from retrieval.models import Document
from retrieval.sample_docs import get_sample_documents
from retrieval.schema import (
    AUTHOR_ROLES,
    CANONICAL_FIELDS,
    CONTENT_HASH_KEY,
    SOURCE_TYPES,
    content_hash,
    normalize_author_role,
    normalize_document,
    normalize_source_type,
)

# One document exactly as data/generator writes it.
GENERATED = {
    "doc_id": "TICKET-40122",
    "doc_type": "ticket",
    "title": "Late delivery - North region electronics",
    "body": "Customer in the North region reports a backlog. Item ELE-LAP-0001.",
    "created_ts": "2026-05-08T09:00:00",
    "region": "North",
    "category": "Electronics",
    "product_id": 1,
    "product_sku": "ELE-LAP-0001",
    "customer_id": 42,
    "rating": None,
    "author_role": "support_agent",
    "resolution_status": "open",
    "period": None,
}


def test_generated_document_normalizes_to_canonical_keys():
    doc = normalize_document(GENERATED)
    assert set(CANONICAL_FIELDS) <= set(doc)
    assert doc["source_type"] == "ticket"
    assert doc["created_at"] == "2026-05-08T09:00:00"
    assert doc["author_role"] == "agent"
    assert doc["region"] == "North"
    assert doc["category"] == "Electronics"


def test_product_ref_prefers_the_sku_over_the_surrogate_id():
    # A SKU is what a lexical/sparse query actually matches on; the integer id
    # is meaningless to a searcher.
    assert normalize_document(GENERATED)["product_ref"] == "ELE-LAP-0001"


def test_product_ref_falls_back_to_the_id_and_is_a_string():
    raw = {k: v for k, v in GENERATED.items() if k != "product_sku"}
    # Qdrant keyword indexes are string-typed; an int would never match.
    assert normalize_document(raw)["product_ref"] == "1"


def test_sample_documents_normalize_unchanged():
    for raw in get_sample_documents():
        doc = normalize_document(raw)
        assert doc["source_type"] == raw["source_type"]
        assert doc["created_at"] == raw["date"]
        assert doc["author_role"] == raw["author_role"]


def test_every_sample_document_uses_the_closed_enums():
    for raw in get_sample_documents():
        doc = normalize_document(raw)
        assert doc["source_type"] in SOURCE_TYPES
        assert doc["author_role"] in AUTHOR_ROLES


def test_every_generated_role_maps_into_the_enum():
    for role in ("support_agent", "customer", "ops_manager"):
        assert normalize_author_role(role) in AUTHOR_ROLES


def test_unknown_role_is_dropped_not_invented():
    # A wrong value is a silent filter mismatch; absence is at least honest.
    assert normalize_author_role("chief vibes officer") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("ticket", "ticket"), ("Support Ticket", "ticket"), ("reviews", "review"),
     ("text", "report"), (None, "document")],
)
def test_source_type_normalization(raw, expected):
    assert normalize_source_type(raw) == expected


# --- content hash -------------------------------------------------------------
def test_content_hash_is_stable_across_spellings():
    """The same document written two ways must hash the same.

    Otherwise every re-index would look like a change and re-embed the corpus.
    """
    other = dict(GENERATED)
    other["source_type"] = other.pop("doc_type")
    other["created_at"] = other.pop("created_ts")
    other["product_ref"] = other.pop("product_sku")
    assert content_hash(GENERATED) == content_hash(other)


def test_content_hash_changes_when_the_body_changes():
    edited = {**GENERATED, "body": GENERATED["body"] + " Escalated."}
    assert content_hash(edited) != content_hash(GENERATED)


def test_content_hash_changes_when_filter_metadata_changes():
    # region/category are filter fields stored in the payload, so a change there
    # must force a rewrite even though the text is identical.
    assert content_hash({**GENERATED, "region": "South"}) != content_hash(GENERATED)


def test_content_hash_ignores_fields_the_index_does_not_use():
    assert content_hash({**GENERATED, "customer_id": 999}) == content_hash(GENERATED)


def test_normalize_recomputes_rather_than_trusting_a_producer_hash():
    doc = normalize_document({**GENERATED, CONTENT_HASH_KEY: "not-a-real-hash"})
    assert doc[CONTENT_HASH_KEY] == content_hash(GENERATED)


# --- Document.from_dict -------------------------------------------------------
def test_document_from_dict_accepts_a_generated_document():
    doc = Document.from_dict(GENERATED)
    assert doc.source_type == "ticket"
    assert doc.date == "2026-05-08T09:00:00"
    assert doc.author_role == "agent"
    assert doc.content_hash == content_hash(GENERATED)


def test_document_from_dict_is_identical_for_both_spellings():
    other = dict(GENERATED)
    other["source_type"] = other.pop("doc_type")
    other["created_at"] = other.pop("created_ts")
    assert Document.from_dict(GENERATED) == Document.from_dict(other)
