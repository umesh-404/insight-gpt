"""Contextual chunk augmentation — embedded text only, never the body or hash.

The augmentation folds the document's region/category into the breadcrumb that
prefixes the EMBEDDED text, so a chunk that never names its own region still
carries that vocabulary. The three invariants under test:

  1. it changes the embedded text (adds the metadata vocabulary);
  2. it leaves the stored ``content`` (quoted back to the user) untouched;
  3. it leaves the content hash untouched, so changed-only re-indexing does not
     churn — the hash is over the ORIGINAL canonical fields, not the augmented
     embedded text.
"""

from __future__ import annotations

import json

from retrieval.chunking import chunk_document, embed_text
from retrieval.config import ChunkingConfig, RetrievalConfig
from retrieval.corpus import STATE_FILENAME, IndexState, load_corpus
from retrieval.indexer import Indexer
from retrieval.models import Document
from retrieval.schema import content_hash

# A ticket whose body deliberately never says "North" or "Electronics"; only its
# metadata carries them. Augmentation is what makes that vocabulary retrievable.
_RAW = {
    "doc_id": "TICKET-1",
    "source_type": "ticket",
    "title": "Delivery delay",
    "body": "The regional warehouse backlog delayed shipments for two weeks.",
    "date": "2026-05-08",
    "region": "North",
    "category": "Electronics",
    "author_role": "agent",
}


def _doc() -> Document:
    return Document.from_dict(_RAW)


def _cfg(augmentation: bool) -> ChunkingConfig:
    return ChunkingConfig(contextual_header=True, contextual_augmentation=augmentation)


def test_augmentation_injects_region_and_category_into_embedded_text():
    doc = _doc()
    chunk = chunk_document(doc, _cfg(True))[0]
    embedded = embed_text(doc, chunk, _cfg(True))
    assert "North" in embedded
    assert "Electronics" in embedded


def test_augmentation_off_omits_region_and_category():
    doc = _doc()
    chunk = chunk_document(doc, _cfg(False))[0]
    embedded = embed_text(doc, chunk, _cfg(False))
    assert "North" not in embedded
    assert "Electronics" not in embedded
    # The breadcrumb header (title/date/source/role) is still present.
    assert "Delivery delay" in embedded


def test_augmentation_never_touches_the_stored_body():
    doc = _doc()
    chunk = chunk_document(doc, _cfg(True))[0]
    # The stored content is the verbatim body — no metadata leaks into it.
    assert chunk.content == _RAW["body"]
    assert "North" not in chunk.content
    assert "Electronics" not in chunk.content


def test_augmentation_does_not_change_the_content_hash():
    # The hash is a function of the canonical fields, independent of embed_text,
    # so turning augmentation on/off cannot change it — otherwise every re-index
    # would churn the whole corpus.
    before = content_hash(_RAW)
    _ = embed_text(_doc(), chunk_document(_doc(), _cfg(True))[0], _cfg(True))
    _ = embed_text(_doc(), chunk_document(_doc(), _cfg(False))[0], _cfg(False))
    assert content_hash(_RAW) == before


def test_optional_doc_context_is_prepended_to_embedded_text_only():
    doc = _doc()
    chunk = chunk_document(doc, _cfg(True))[0]
    embedded = embed_text(doc, chunk, _cfg(True), doc_context="North fulfilment backlog")
    assert "[context: North fulfilment backlog]" in embedded
    # Still absent from the stored body.
    assert "context:" not in chunk.content


# --- changed-only indexing stays a no-op with augmentation enabled ------------


class _FakeStore:
    def __init__(self) -> None:
        self.written: list[str] = []
        self.deleted: list[str] = []

    def delete_document(self, doc_id: str) -> None:
        self.deleted.append(doc_id)

    def write_document(self, doc, chunks, dense, sparse) -> int:
        self.written.append(doc.doc_id)
        return len(chunks)


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        return [[0.0] * 8 for _ in texts]


def _run(cfg, corpus_file, state_path):
    store, embedder = _FakeStore(), _FakeEmbedder()
    state = IndexState(state_path, cfg.collection)
    stats = Indexer(cfg, store, embedder).index_changed(load_corpus(corpus_file), state)
    return stats, store, embedder


def test_reindex_is_a_noop_when_only_augmentation_derived_text_would_change(tmp_path):
    cfg = RetrievalConfig()
    cfg.chunking.contextual_augmentation = True
    corpus_file = tmp_path / "documents.json"
    corpus_file.write_text(json.dumps([_RAW]), encoding="utf-8")
    state_path = tmp_path / STATE_FILENAME

    stats1, store1, emb1 = _run(cfg, corpus_file, state_path)
    assert store1.written == ["TICKET-1"]
    assert emb1.calls > 0

    # Second run over an unchanged corpus: augmentation is derived, the hash is
    # over the original fields, so nothing is re-embedded.
    stats2, store2, emb2 = _run(cfg, corpus_file, state_path)
    assert store2.written == []
    assert emb2.calls == 0
    assert stats2.skipped_unchanged == 1
