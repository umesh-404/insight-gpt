"""Retrieval evaluation — measured, not asserted (docs/04 §8, docs/00 §7).

A golden set of ``question -> expected-doc(s)`` pairs, scored against the live
pipeline with the standard minimum metrics:

    Recall@1 / Recall@3  -- is an expected document the top hit / in the top k?
    MRR                  -- how high does the first correct document rank?

Runs the queries twice — reranking off vs. on — so the second stage has to prove
its keep with numbers (rerank lift). Mirrors ``rememory/tests/eval_retrieval.py``.
Needs live Qdrant + Ollama; the offline unit tests cover the pure components.
"""

from __future__ import annotations

import math
import sys
import time
from collections import Counter

from .chunking import chunk_document, embed_text
from .config import RetrievalConfig, get_config
from .models import Document
from .retriever import QdrantRetriever
from .rewrite import QueryRewriter
from .sample_docs import get_sample_documents
from .sparse import build_sparse_vector

# Two golden sets, because there are two indexable corpora and their doc ids do
# not overlap in meaning:
#
#   SAMPLE_GOLDEN    -- fixed doc ids from retrieval.sample_docs
#   CORPUS_GOLDEN    -- the generated corpus, where ids are arbitrary but the
#                       planted story is not, so a case is judged by METADATA
#                       (region/category/source_type) instead of by id.
#
# A case may carry ``expect`` (doc ids), ``expect_meta`` (all key/value pairs
# must match the hit), or both — a hit satisfying either counts.

# question -> the doc_ids that genuinely answer it. Drawn from the sample corpus.
SAMPLE_GOLDEN: list[dict] = [
    {
        "query": "why are North region electronics deliveries late?",
        "expect": {"TICKET-40122", "TICKET-40210", "REPORT-Q2-OPS", "REVIEW-9931"},
    },
    {
        "query": "reviews for the X230 laptop",
        "expect": {"REVIEW-9931", "TICKET-40122"},
    },
    {
        "query": "what did the Q2 operations review say about fulfilment?",
        "expect": {"REPORT-Q2-OPS"},
    },
    {
        "query": "fulfilment centre backlog root cause",
        "expect": {"REPORT-Q2-OPS", "TICKET-40210"},
    },
    {
        "query": "apparel shipping in the South",
        "expect": {"REVIEW-9950"},
    },
]

# The generated corpus (data/ingested/documents.json). Ids there are sequential
# and meaningless, so correctness is judged on the planted story's metadata: the
# 2026Q2 North/Electronics fulfilment backlog.
CORPUS_GOLDEN: list[dict] = [
    {
        "query": "why are North region electronics deliveries late?",
        "expect_meta": {"region": "North", "category": "Electronics"},
    },
    {
        "query": "customer complaints about the North warehouse backlog",
        "expect_meta": {"region": "North", "category": "Electronics"},
    },
    {
        "query": "what did the 2026Q2 operations review say?",
        "expect": {"REPORT-2026Q2"},
    },
    {
        "query": "fulfilment centre backlog root cause",
        "expect_meta": {"region": "North", "category": "Electronics"},
    },
]

# Backwards-compatible alias: the sample set is what `eval` scored before.
GOLDEN = SAMPLE_GOLDEN


def _matches(result, case: dict) -> bool:
    """Does this hit satisfy the case — by doc id, or by expected metadata?"""
    if result.doc_id in case.get("expect", ()):
        return True
    expect_meta = case.get("expect_meta")
    if not expect_meta:
        return False
    return all(result.metadata.get(key) == value for key, value in expect_meta.items())


def evaluate(retriever: QdrantRetriever, cases: list[dict], *, k: int = 5) -> dict:
    hits1 = hits3 = 0
    rr_sum = 0.0
    misses: list[str] = []
    t0 = time.perf_counter()

    for case in cases:
        results = retriever.search(case["query"], k=k)
        rank = next(
            (i for i, r in enumerate(results, 1) if _matches(r, case)),
            None,
        )
        if rank == 1:
            hits1 += 1
        if rank is not None and rank <= 3:
            hits3 += 1
        rr_sum += (1 / rank) if rank else 0.0
        if rank != 1:
            got = results[0].doc_id if results else "(nothing)"
            misses.append(f"    rank {rank or '>k'}: {case['query'][:52]!r} -> {got}")

    n = len(cases)
    return {
        "recall@1": hits1 / n,
        "recall@3": hits3 / n,
        "mrr": rr_sum / n,
        "avg_latency_s": (time.perf_counter() - t0) / n,
        "misses": misses,
    }


def run(
    config: RetrievalConfig | None = None,
    floor: float = 0.80,
    *,
    samples: bool = False,
) -> int:
    """Score a golden set with rerank off vs. on. Returns a process exit code.

    Which golden set depends on what is in the collection: ``samples=True``
    scores the demo documents, otherwise the generated-corpus set is used —
    matching ``insight-retrieval index`` and ``index --samples`` respectively.
    """
    cfg = config or get_config()
    retriever = QdrantRetriever(cfg)
    cases = SAMPLE_GOLDEN if samples else CORPUS_GOLDEN
    corpus = "sample documents" if samples else "the generated corpus"

    print(f"{len(cases)} golden queries against the live index ({corpus})\n")
    rows: dict[str, dict] = {}
    for label, rerank in (("RRF only", False), ("RRF + rerank", True)):
        retriever.searcher.reranker.enabled = rerank and cfg.reranker.enabled
        rows[label] = evaluate(retriever, cases)

    print(f"{'pipeline':<14}{'R@1':>8}{'R@3':>8}{'MRR':>8}{'avg s':>8}")
    for label, m in rows.items():
        print(
            f"{label:<14}{m['recall@1']:>8.0%}{m['recall@3']:>8.0%}"
            f"{m['mrr']:>8.3f}{m['avg_latency_s']:>8.2f}"
        )
    for label, m in rows.items():
        if m["misses"]:
            print(f"\n  not-top-1 ({label}):")
            for line in m["misses"]:
                print(line)

    ok = rows["RRF + rerank"]["recall@3"] >= floor
    print(
        f"\n{'PASS' if ok else 'FAIL'}: reranked recall@3 "
        f"{rows['RRF + rerank']['recall@3']:.0%} (floor {floor:.0%})"
    )
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Offline proxy: a deterministic, no-services evaluation of the two lexical-
# visible enhancements (query rewriting, contextual augmentation).
#
# It CANNOT measure the dense branch (that needs Ollama), so it is not the live
# number — it is the sparse/lexical path scored with IDF, which is exactly the
# half of the pipeline the two enhancements touch in text. Labeled "offline
# proxy" everywhere so it is never mistaken for the live pipeline's score. The
# numbers are computed here, not asserted, so they are real.
# ---------------------------------------------------------------------------


# A purpose-built, synthetic proxy fixture. SAMPLE_GOLDEN's tiny corpus is
# lexically saturated — every relevant document already spells out its own
# region/category, so the lexical baseline scores it perfectly and leaves no
# headroom for a delta. These documents are constructed so the baseline lexical
# path FAILS in exactly the way each enhancement is meant to fix:
#
#   * PROXY-AUG's body never names its own region/category, so "north
#     electronics ..." matches it only once contextual augmentation folds the
#     metadata into the embedded text.
#   * PROXY-REW spells out "fulfilment centre" while the query abbreviates it
#     "FC", so it wins only once query rewriting expands the abbreviation.
#
# This is a controlled mechanism demonstration, clearly NOT the live pipeline
# and NOT the product corpus; the numbers are computed, never asserted.
PROXY_CORPUS: list[dict] = [
    {
        "doc_id": "PROXY-AUG",
        "source_type": "ticket",
        "title": "Delivery delay",
        "body": "The regional warehouse backlog delayed shipments for two weeks "
        "and drove cancellations.",
        "region": "North",
        "category": "Electronics",
        "author_role": "agent",
    },
    {
        "doc_id": "PROXY-DIST1",
        "source_type": "review",
        "title": "Weekend deals",
        "body": "Great electronics deals this weekend, limited stock, grab them fast.",
        "region": "East",
        "category": "Home",
        "author_role": "customer",
    },
    {
        "doc_id": "PROXY-REW",
        "source_type": "report",
        "title": "Ops note",
        "body": "The fulfilment centre bottleneck persisted through May and slowed dispatch.",
        "region": "West",
        "category": "Home",
        "author_role": "manager",
    },
    {
        "doc_id": "PROXY-DIST2",
        "source_type": "ticket",
        "title": "Returns",
        "body": "Bottleneck bottleneck bottleneck in returns processing, urgent "
        "bottleneck escalation.",
        "region": "South",
        "category": "Apparel",
        "author_role": "agent",
    },
    {
        "doc_id": "PROXY-DIST3",
        "source_type": "review",
        "title": "Nice apparel",
        "body": "Lovely apparel range in the South, fast shipping, no problems whatsoever.",
        "region": "South",
        "category": "Apparel",
        "author_role": "customer",
    },
]

PROXY_GOLDEN: list[dict] = [
    {"query": "north electronics complaints", "expect": {"PROXY-AUG"}},
    {"query": "FC bottleneck", "expect": {"PROXY-REW"}},
]


def _sample_docs() -> list[Document]:
    return [Document.from_dict(d) for d in get_sample_documents()]


def _chunk_vectors(
    docs: list[Document], cfg: RetrievalConfig, *, augmentation: bool
) -> list[tuple[str, dict[int, float]]]:
    """Sparse vector per chunk (doc_id, {index: weight}), as the store would hold.

    ``augmentation`` toggles the contextual region/category breadcrumb so the
    proxy can score the corpus with and without it.
    """
    chunk_cfg = cfg.chunking.model_copy(update={"contextual_augmentation": augmentation})
    out: list[tuple[str, dict[int, float]]] = []
    for doc in docs:
        for chunk in chunk_document(doc, chunk_cfg):
            text = embed_text(doc, chunk, chunk_cfg)
            indices, values = build_sparse_vector(text, cfg.sparse)
            out.append((doc.doc_id, dict(zip(indices, values, strict=True))))
    return out


def _idf(vectors: list[tuple[str, dict[int, float]]]) -> dict[int, float]:
    """Inverse chunk-frequency per term — the weighting Qdrant applies server-side."""
    n = len(vectors) or 1
    df: Counter[int] = Counter()
    for _doc_id, vec in vectors:
        df.update(vec.keys())
    return {idx: math.log(1 + n / (1 + freq)) for idx, freq in df.items()}


def _rank_docs(
    query_vec: dict[int, float],
    vectors: list[tuple[str, dict[int, float]]],
    idf: dict[int, float],
) -> list[str]:
    """Rank doc ids by best IDF-weighted lexical overlap of any of their chunks."""
    best: dict[str, float] = {}
    for doc_id, vec in vectors:
        score = sum(
            w * vec.get(idx, 0.0) * idf.get(idx, 0.0) ** 2 for idx, w in query_vec.items()
        )
        if score > best.get(doc_id, 0.0):
            best[doc_id] = score
    ranked = [d for d, s in sorted(best.items(), key=lambda kv: kv[1], reverse=True) if s > 0]
    return ranked


def evaluate_offline(
    cases: list[dict],
    docs: list[Document],
    cfg: RetrievalConfig,
    *,
    rewrite: bool,
    augmentation: bool,
    k: int = 5,
) -> dict:
    """Lexical-proxy Recall@1/@3 and MRR for one (rewrite, augmentation) setting."""
    vectors = _chunk_vectors(docs, cfg, augmentation=augmentation)
    idf = _idf(vectors)
    rewriter = QueryRewriter(cfg.query_rewrite, cfg.embedding)

    hits1 = hits3 = 0
    rr_sum = 0.0
    for case in cases:
        query = case["query"]
        text = rewriter.deterministic(query) if rewrite else query
        q_indices, q_values = build_sparse_vector(text, cfg.sparse)
        ranked = _rank_docs(dict(zip(q_indices, q_values, strict=True)), vectors, idf)[:k]
        expect = case.get("expect", set())
        rank = next((i for i, doc_id in enumerate(ranked, 1) if doc_id in expect), None)
        if rank == 1:
            hits1 += 1
        if rank is not None and rank <= 3:
            hits3 += 1
        rr_sum += (1 / rank) if rank else 0.0

    n = len(cases) or 1
    return {"recall@1": hits1 / n, "recall@3": hits3 / n, "mrr": rr_sum / n}


_PROXY_SETTINGS: tuple[tuple[str, bool, bool], ...] = (
    ("baseline (neither)", False, False),
    ("+ query rewrite", True, False),
    ("+ augmentation", False, True),
    ("+ both", True, True),
)


def _scoreboard(title: str, cases: list[dict], docs: list[Document], cfg: RetrievalConfig) -> None:
    print(title)
    print(f"{'setting':<22}{'R@1':>8}{'R@3':>8}{'MRR':>8}   delta-vs-baseline")
    base: dict | None = None
    for label, rewrite, augmentation in _PROXY_SETTINGS:
        m = evaluate_offline(cases, docs, cfg, rewrite=rewrite, augmentation=augmentation)
        if base is None:
            base = m
            delta = ""
        else:
            delta = (
                f"   dMRR {m['mrr'] - base['mrr']:+.3f}, "
                f"dR@1 {m['recall@1'] - base['recall@1']:+.0%}"
            )
        print(
            f"{label:<22}{m['recall@1']:>8.0%}{m['recall@3']:>8.0%}{m['mrr']:>8.3f}{delta}"
        )
    print()


def run_offline_proxy(config: RetrievalConfig | None = None) -> int:
    """Score the four (rewrite x augmentation) settings, no Qdrant, no Ollama.

    A deterministic lower bound that isolates the LEXICAL contribution of each
    enhancement (it cannot see the dense branch — that needs Ollama). Two boards:
    a purpose-built fixture with headroom that shows the real per-mechanism
    delta, and the live sample golden set to confirm no regression at ceiling.
    Always returns 0 (reporting, not gating).
    """
    cfg = config or get_config()
    print(
        "OFFLINE PROXY - lexical/sparse path only, no models. Not the live "
        "pipeline score;\nit isolates what query-rewrite and augmentation change "
        "in the retrievable text.\n"
    )
    _scoreboard(
        f"[1] synthetic fixture with headroom ({len(PROXY_GOLDEN)} queries) - "
        "shows the per-mechanism delta:",
        PROXY_GOLDEN,
        [Document.from_dict(d) for d in PROXY_CORPUS],
        cfg,
    )
    sample_cases = [c for c in SAMPLE_GOLDEN if c.get("expect")]
    _scoreboard(
        f"[2] sample golden set ({len(sample_cases)} queries) - lexically "
        "saturated, kept to prove no regression:",
        sample_cases,
        _sample_docs(),
        cfg,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
