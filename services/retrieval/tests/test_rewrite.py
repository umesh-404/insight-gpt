"""Deterministic query rewriting — no network.

Only the deterministic path is exercised here: it is a pure function of
``(query, config)`` and must never touch Ollama. The LLM/HyDE paths are gated
behind ``use_llm`` + live Ollama (``RETRIEVAL_LIVE=1``) and are not unit-tested
offline by design.
"""

from __future__ import annotations

from retrieval.config import QueryRewriteConfig, RetrievalConfig
from retrieval.rewrite import QueryRewriter, RewrittenQuery


def _rw(**over) -> QueryRewriter:
    cfg = QueryRewriteConfig(**over)
    return QueryRewriter(cfg, RetrievalConfig().embedding)


def test_drops_filler_and_stopwords_keeps_content():
    out = _rw().deterministic("why are the North region electronics deliveries late?")
    tokens = out.split()
    # Question scaffolding and stopwords are gone...
    assert "why" not in tokens
    assert "are" not in tokens
    assert "the" not in tokens
    # ...content words survive (lowercased).
    for word in ("north", "region", "electronics", "deliveries", "late"):
        assert word in tokens


def test_preserves_identifier_entities_verbatim():
    out = _rw().deterministic("show me order ORD-88213 for SKU X230")
    # Identifier-shaped tokens are kept exactly, not lowercased or split away.
    assert "ORD-88213" in out.split()
    assert "X230" in out.split()


def test_expands_known_abbreviations_and_keeps_the_abbreviation():
    out = _rw().deterministic("FC backlog eta")
    tokens = out.split()
    # Abbreviation kept AND expanded, even though "FC" is all-caps.
    assert "fc" in tokens
    assert "fulfilment" in tokens and "centre" in tokens
    # eta -> estimated time of arrival
    assert "eta" in tokens
    assert "estimated" in tokens


def test_custom_abbreviations_merge_over_defaults():
    out = _rw(abbreviations={"wms": "warehouse management system"}).deterministic("wms outage")
    tokens = out.split()
    assert "warehouse" in tokens and "management" in tokens and "system" in tokens
    assert "outage" in tokens


def test_all_stopword_query_falls_back_to_lowercased_original():
    # Stripping everything would hand retrieval an empty string; guard against it.
    out = _rw().deterministic("why is it")
    assert out  # non-empty
    assert out == "why is it".lower()


def test_rewrite_wraps_result_and_keeps_original():
    r = _rw().rewrite("Why are North electronics deliveries late?")
    assert isinstance(r, RewrittenQuery)
    assert r.method == "deterministic"
    assert r.original == "Why are North electronics deliveries late?"
    assert "north" in r.search_query.split()
    assert r.hyde is None


def test_disabled_rewriter_is_a_passthrough():
    r = _rw(enabled=False).rewrite("Why are deliveries late?")
    assert r.search_query == "Why are deliveries late?"
    assert r.method == "deterministic"


def test_empty_query_is_safe():
    r = _rw().rewrite("   ")
    assert r.search_query == ""
    assert r.hyde is None


def test_llm_path_not_attempted_without_a_model():
    # use_llm on but model empty -> chat is unusable -> deterministic result,
    # never a network call.
    r = _rw(use_llm=True, model="").rewrite("FC backlog")
    assert r.method == "deterministic"
    assert "fulfilment" in r.search_query.split()
