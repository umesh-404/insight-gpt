"""Contract §3 — the guards that keep another project's data alive.

Qdrant on 6333 is shared: it holds ``code``, ``docs`` and ``memory`` for a
different project on this machine, and a single wrong delete is unrecoverable.
Two guards are asserted here, and neither needs a running Qdrant:

* this suite's own :func:`assert_test_collection`, which allows nothing but
  ``insightgpt_test_*``;
* the API's ``datasets_store._assert_ours``, which refuses to index into or
  delete from any collection without the ``insightgpt_`` prefix.

A guard nobody tests is a comment.
"""

from __future__ import annotations

import pytest

from .qdrant_safety import (
    FOREIGN_COLLECTIONS,
    UnsafeCollectionError,
    assert_test_collection,
    scratch_collection_name,
)


@pytest.mark.parametrize(
    "name",
    [
        *FOREIGN_COLLECTIONS,
        "insightgpt_documents",          # ours, but production — tests never drop it
        "insightgpt_test",               # prefix without a scratch name
        "insightgpt_",
        "documents",
        "",
        "*",
        "insightgpt_test_x; drop",
        "../insightgpt_test_x",
        "INSIGHTGPT_TEST_X",             # the prefix is case-sensitive on purpose
    ],
)
def test_the_suite_refuses_to_drop_anything_but_its_own_scratch(name: str) -> None:
    with pytest.raises(UnsafeCollectionError):
        assert_test_collection(name)


def test_non_string_collection_names_are_refused() -> None:
    for value in (None, 0, ["insightgpt_test_x"]):
        with pytest.raises(UnsafeCollectionError):
            assert_test_collection(value)  # type: ignore[arg-type]


def test_scratch_names_are_accepted_and_process_scoped() -> None:
    name = scratch_collection_name("roundtrip")
    assert name.startswith("insightgpt_test_roundtrip_")
    assert assert_test_collection(name) == name
    # Two concurrent runs must not share one collection and delete each other's
    # points, so the pid is part of the name.
    assert name == scratch_collection_name("roundtrip")


def test_the_api_refuses_to_index_outside_its_own_collections() -> None:
    """``datasets_store._assert_ours`` is the guard between an upload and the
    shared instance. Checked here because the API's own suite cannot: the
    message it raises is what tells an operator to set QDRANT_COLLECTION."""
    pytest.importorskip("fastapi", reason="needs the services/api environment")
    from app.api import datasets_store as store

    assert store.COLLECTION_PREFIX == "insightgpt_"
    for name in (*FOREIGN_COLLECTIONS, "documents", "", "insight_gpt_documents"):
        with pytest.raises(RuntimeError) as raised:
            store._assert_ours(name)
        assert "shared" in str(raised.value).lower()
        assert "QDRANT_COLLECTION" in str(raised.value)

    # And it allows our own, both production and scratch.
    store._assert_ours("insightgpt_documents")
    store._assert_ours(scratch_collection_name("guard"))
