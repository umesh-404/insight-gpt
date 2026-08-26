"""The offline-proxy harness produces real, non-negative deltas — no services.

This guards the "prove it" claim: the lexical proxy must actually show each
enhancement helping on the purpose-built fixture, and must never regress the
lexically-saturated sample set. Pure computation, no Qdrant/Ollama.
"""

from __future__ import annotations

from retrieval.config import RetrievalConfig
from retrieval.eval import (
    PROXY_CORPUS,
    PROXY_GOLDEN,
    SAMPLE_GOLDEN,
    evaluate_offline,
    run_offline_proxy,
)
from retrieval.models import Document
from retrieval.sample_docs import get_sample_documents


def _proxy_docs() -> list[Document]:
    return [Document.from_dict(d) for d in PROXY_CORPUS]


def _score(rewrite: bool, augmentation: bool) -> dict:
    return evaluate_offline(
        PROXY_GOLDEN,
        _proxy_docs(),
        RetrievalConfig(),
        rewrite=rewrite,
        augmentation=augmentation,
    )


def test_each_enhancement_helps_and_both_is_best():
    base = _score(False, False)
    rewrite = _score(True, False)
    augment = _score(False, True)
    both = _score(True, True)

    # Baseline fails on the fixture (that is the point of the fixture)...
    assert base["mrr"] < both["mrr"]
    # ...each enhancement helps on its own...
    assert rewrite["mrr"] > base["mrr"]
    assert augment["mrr"] > base["mrr"]
    # ...and both together is the strongest, and solves the fixture.
    assert both["recall@1"] == 1.0
    assert both["mrr"] == 1.0


def test_sample_set_does_not_regress():
    cases = [c for c in SAMPLE_GOLDEN if c.get("expect")]
    docs = [Document.from_dict(d) for d in get_sample_documents()]
    base = evaluate_offline(cases, docs, RetrievalConfig(), rewrite=False, augmentation=False)
    both = evaluate_offline(cases, docs, RetrievalConfig(), rewrite=True, augmentation=True)
    # Enhancements must never make the saturated baseline worse.
    assert both["recall@1"] >= base["recall@1"]
    assert both["mrr"] >= base["mrr"]


def test_run_offline_proxy_returns_zero(capsys):
    assert run_offline_proxy(RetrievalConfig()) == 0
    out = capsys.readouterr().out
    assert "OFFLINE PROXY" in out
    assert "query rewrite" in out
    assert "augmentation" in out
