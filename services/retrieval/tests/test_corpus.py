"""Corpus loading + changed-only index state (the hand-off's incremental half).

Embedding is the expensive step in the whole pipeline, so the value of the
hand-off is not just that ``reindex_docs`` reads the real corpus — it is that a
scheduled reindex over an unchanged corpus costs nothing, and that a document
deleted upstream stops being retrievable.
"""

from __future__ import annotations

import json

import pytest

from retrieval.corpus import (
    STATE_FILENAME,
    ChangeSet,
    IndexState,
    default_corpus_path,
    load_corpus,
    state_path_for,
)
from retrieval.models import Document

GENERATED = [
    {
        "doc_id": "TICKET-2",
        "doc_type": "ticket",
        "title": "Late delivery",
        "body": "North backlog.",
        "created_ts": "2026-05-08T09:00:00",
        "region": "North",
        "category": "Electronics",
        "author_role": "support_agent",
    },
    {
        "doc_id": "REVIEW-1",
        "doc_type": "review",
        "title": "Great",
        "body": "Fast shipping.",
        "created_ts": "2026-05-11T12:00:00",
        "region": "South",
        "category": "Apparel",
        "author_role": "customer",
    },
]


@pytest.fixture
def corpus_file(tmp_path):
    path = tmp_path / "documents.json"
    path.write_text(json.dumps(GENERATED), encoding="utf-8")
    return path


def _state(tmp_path, collection="documents") -> IndexState:
    return IndexState(tmp_path / STATE_FILENAME, collection)


# --- loading ------------------------------------------------------------------
def test_load_corpus_normalizes_and_sorts(corpus_file):
    docs = load_corpus(corpus_file)
    assert [d.doc_id for d in docs] == ["REVIEW-1", "TICKET-2"]
    assert {d.source_type for d in docs} == {"ticket", "review"}
    assert {d.author_role for d in docs} == {"agent", "customer"}


def test_load_corpus_reads_a_folder_of_json_files(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([GENERATED[0]]), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps([GENERATED[1]]), encoding="utf-8")
    assert len(load_corpus(tmp_path)) == 2


def test_load_corpus_skips_the_state_file_in_a_folder(tmp_path):
    """The state file lives beside the corpus; it is not a document."""
    (tmp_path / "a.json").write_text(json.dumps(GENERATED), encoding="utf-8")
    _state(tmp_path).save()
    assert len(load_corpus(tmp_path)) == 2


def test_default_corpus_path_matches_the_ingestion_contract():
    assert default_corpus_path().parts[-3:] == ("data", "ingested", "documents.json")


def test_state_path_sits_beside_the_corpus(tmp_path):
    assert state_path_for(tmp_path / "documents.json") == tmp_path / STATE_FILENAME


# --- changed-only planning ----------------------------------------------------
def test_first_run_indexes_everything(corpus_file, tmp_path):
    plan = _state(tmp_path).plan(load_corpus(corpus_file))
    assert len(plan.to_index) == 2
    assert plan.unchanged == 0
    assert plan.removed == []


def test_second_run_over_an_unchanged_corpus_indexes_nothing(corpus_file, tmp_path):
    docs = load_corpus(corpus_file)
    state = _state(tmp_path)
    state.record(docs)
    state.save()

    plan = IndexState(tmp_path / STATE_FILENAME, "documents").plan(docs)
    assert plan.to_index == []
    assert plan.unchanged == 2


def test_only_the_edited_document_is_re_indexed(corpus_file, tmp_path):
    state = _state(tmp_path)
    state.record(load_corpus(corpus_file))
    state.save()

    edited = [dict(GENERATED[0], body="North backlog. Escalated."), GENERATED[1]]
    corpus_file.write_text(json.dumps(edited), encoding="utf-8")

    plan = IndexState(tmp_path / STATE_FILENAME, "documents").plan(load_corpus(corpus_file))
    assert [d.doc_id for d in plan.to_index] == ["TICKET-2"]
    assert plan.unchanged == 1


def test_a_document_removed_upstream_is_scheduled_for_deletion(corpus_file, tmp_path):
    state = _state(tmp_path)
    state.record(load_corpus(corpus_file))
    state.save()

    corpus_file.write_text(json.dumps([GENERATED[1]]), encoding="utf-8")
    plan = IndexState(tmp_path / STATE_FILENAME, "documents").plan(load_corpus(corpus_file))
    assert plan.removed == ["TICKET-2"]
    assert plan.to_index == []


def test_full_run_ignores_the_state(corpus_file, tmp_path):
    state = _state(tmp_path)
    state.record(load_corpus(corpus_file))
    state.save()
    plan = state.plan(load_corpus(corpus_file), full=True)
    assert len(plan.to_index) == 2
    assert plan.unchanged == 0


def test_state_from_another_collection_is_ignored(corpus_file, tmp_path):
    """Hashes say what a SPECIFIC collection holds, nothing about another one."""
    state = _state(tmp_path, collection="documents")
    state.record(load_corpus(corpus_file))
    state.save()

    other = IndexState(tmp_path / STATE_FILENAME, "documents_v2")
    assert len(other.plan(load_corpus(corpus_file)).to_index) == 2


def test_corrupt_state_falls_back_to_a_full_index(corpus_file, tmp_path):
    # Failing towards "re-index" is the safe direction; failing towards "skip"
    # would leave the collection permanently stale.
    (tmp_path / STATE_FILENAME).write_text("{not json", encoding="utf-8")
    plan = _state(tmp_path).plan(load_corpus(corpus_file))
    assert len(plan.to_index) == 2


def test_forget_drops_removed_ids(tmp_path):
    state = _state(tmp_path)
    state.record([Document(doc_id="X", source_type="ticket", title="", body="",
                           content_hash="abc")])
    state.forget(["X"])
    state.save()
    assert IndexState(tmp_path / STATE_FILENAME, "documents").hashes == {}


def test_changeset_total_counts_the_whole_corpus():
    changeset = ChangeSet(to_index=[object()], unchanged=3)
    assert changeset.total == 4
