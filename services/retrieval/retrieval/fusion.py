"""Reciprocal Rank Fusion — the math Qdrant runs server-side, in the open.

At query time the primary pipeline lets Qdrant fuse the dense and sparse
candidate lists itself (``models.FusionQuery(fusion=Fusion.RRF)``), which is
faster and keeps everything in one round trip. This module reimplements the same
fusion in pure Python for two reasons:

1. It is the reference for *why* RRF is the right choice, and it is unit-tested
   offline — no Qdrant needed to verify the ranking behaviour.
2. It backs the client-side fallback when the two branches are fetched
   separately (e.g. a degraded path), so fusion stays available even if the
   server-side query cannot run.

Why RRF and not a weighted score blend: cosine similarities and sparse
term-frequency scores live on incomparable scales, so averaging their raw values
is meaningless and needs fragile hand-tuned weights. RRF scores each item by its
RANK in each list — the only thing the two lists share — so a document ranked
highly by either branch rises, with no scale calibration.

    score(d) = Σ_lists  1 / (k + rank_in_list(d))      (rank is 1-based)

``k`` (default 60, the value from the original Cormack et al. paper and Qdrant's
own default) damps the contribution of top ranks so a single first place does
not dominate a document that places well in both lists.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = DEFAULT_K,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one ranking, best first.

    Each inner sequence is an ordered list of ids (rank 1 = index 0). Returns
    ``(id, fused_score)`` pairs sorted by descending score. Ties break by first
    appearance across the input lists, so the result is deterministic.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            if item not in first_seen:
                first_seen[item] = order
                order += 1

    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
