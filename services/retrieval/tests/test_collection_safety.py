"""The guard that stands between a config typo and another project's data.

Qdrant is routinely run as ONE shared instance on a developer machine. This
deployment's Qdrant also holds collections belonging to an unrelated local
project. So the rule is not "be careful": it is that a delete against anything
outside the ``insightgpt_`` namespace must fail, in code, before it reaches the
wire — and that this package contains no collection-drop call at all.

These tests use no Qdrant. They assert on the guard and on the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrieval.store import (
    COLLECTION_PREFIX,
    CollectionSafetyError,
    Store,
    assert_insightgpt_collection,
)

_PACKAGE = Path(__file__).resolve().parents[1] / "retrieval"

# Names a shared instance plausibly holds that are NOT ours. The third is the
# one that matters most: "docs" is a near-miss for a generic "documents".
_FOREIGN = ["code", "docs", "memory", "documents", "", "insight_gpt_documents"]


@pytest.mark.parametrize("name", _FOREIGN)
def test_delete_is_refused_outside_our_namespace(name):
    with pytest.raises(CollectionSafetyError) as excinfo:
        assert_insightgpt_collection(name)
    # The message has to tell the operator what to fix, not just that it failed.
    assert COLLECTION_PREFIX in str(excinfo.value)


@pytest.mark.parametrize("name", ["insightgpt_documents", "insightgpt_test_scratch"])
def test_our_own_collections_are_allowed(name):
    assert assert_insightgpt_collection(name) is None


def test_the_guard_runs_before_any_delete_reaches_qdrant(monkeypatch):
    """A mis-configured collection must fail WITHOUT a client call.

    ``Store`` is built with a stub client, so if the delete path ever reached
    Qdrant this test would record the call instead of raising.
    """
    calls: list[tuple] = []

    class ExplodingClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def delete(self, **kw):
            calls.append(("delete", kw))

        def collection_exists(self, name):
            calls.append(("collection_exists", name))
            return True

        def count(self, **kw):
            calls.append(("count", kw))
            raise AssertionError("count must not run for a foreign collection")

    monkeypatch.setattr("retrieval.store.QdrantClient", ExplodingClient)

    from retrieval.config import RetrievalConfig

    store = Store(RetrievalConfig(collection="docs"))
    with pytest.raises(CollectionSafetyError):
        store.delete_document("d1")
    with pytest.raises(CollectionSafetyError):
        store.delete_dataset("ds_abc")
    # Writes too: adopting a foreign collection of the same vector width and
    # upserting into it is quieter than a delete and just as destructive.
    with pytest.raises(CollectionSafetyError):
        store.ensure_collection()
    with pytest.raises(CollectionSafetyError):
        store.write_document(_doc(), [], [], None)
    assert calls == []


def _doc():
    from retrieval.models import Document

    return Document(doc_id="d1", source_type="report", title="t", body="b")


def test_the_package_contains_no_collection_drop_at_all():
    """Grep is the test. There is no safe reason for these calls to exist here.

    A guard can be bypassed by a future caller; a call that is not in the
    source cannot be made at all. If a legitimate need ever appears, deleting
    this test is a deliberate, reviewable act.
    """
    forbidden = ("delete_collection", "recreate_collection", "delete_collection_aliases")
    offenders: list[str] = []
    for source in sorted(_PACKAGE.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        for call in forbidden:
            if call in text:
                offenders.append(f"{source.name}: {call}")
    assert offenders == [], f"collection-dropping calls found: {offenders}"
