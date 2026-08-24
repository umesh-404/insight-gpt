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

import sys
import time

from .config import RetrievalConfig, get_config
from .retriever import QdrantRetriever

# question -> the doc_ids that genuinely answer it. Drawn from the sample corpus.
GOLDEN: list[dict] = [
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


def evaluate(retriever: QdrantRetriever, cases: list[dict], *, k: int = 5) -> dict:
    hits1 = hits3 = 0
    rr_sum = 0.0
    misses: list[str] = []
    t0 = time.perf_counter()

    for case in cases:
        results = retriever.search(case["query"], k=k)
        rank = next(
            (i for i, r in enumerate(results, 1) if r.doc_id in case["expect"]),
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


def run(config: RetrievalConfig | None = None, floor: float = 0.80) -> int:
    """Score the golden set with rerank off vs. on. Returns a process exit code."""
    cfg = config or get_config()
    retriever = QdrantRetriever(cfg)

    print(f"{len(GOLDEN)} golden queries against the live index\n")
    rows: dict[str, dict] = {}
    for label, rerank in (("RRF only", False), ("RRF + rerank", True)):
        retriever.searcher.reranker.enabled = rerank and cfg.reranker.enabled
        rows[label] = evaluate(retriever, GOLDEN)

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


if __name__ == "__main__":
    sys.exit(run())
