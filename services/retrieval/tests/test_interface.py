"""The engine-facing contract must not drift.

The insight engine depends on ``QdrantRetriever.search(query, *, filters, k)``
returning ``list[RetrievedDoc]`` with a fixed field set. Query rewriting and
contextual augmentation are internal steps and must not change that surface.
This test fails loudly if the signature or the model shape ever changes.
"""

from __future__ import annotations

import inspect

from retrieval.models import RetrievedDoc
from retrieval.retriever import QdrantRetriever


def test_search_signature_is_unchanged():
    sig = inspect.signature(QdrantRetriever.search)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["self", "query", "filters", "k"]

    # query is positional; filters and k are keyword-only with their defaults.
    assert params[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    filters = sig.parameters["filters"]
    k = sig.parameters["k"]
    assert filters.kind is inspect.Parameter.KEYWORD_ONLY
    assert filters.default is None
    assert k.kind is inspect.Parameter.KEYWORD_ONLY
    assert k.default == 5


def test_retrieved_doc_field_set_is_unchanged():
    fields = set(RetrievedDoc.model_fields)
    assert fields == {
        "doc_id",
        "source_type",
        "title",
        "body",
        "date",
        "score",
        "metadata",
    }
