"""RRF fusion math: rank-based scoring, cross-list boosting, determinism."""

from __future__ import annotations

from retrieval.fusion import DEFAULT_K, reciprocal_rank_fusion


def test_single_list_preserves_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [item for item, _ in fused] == ["a", "b", "c"]


def test_score_formula_matches_definition():
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=DEFAULT_K))
    assert fused["a"] == 1.0 / (DEFAULT_K + 1)
    assert fused["b"] == 1.0 / (DEFAULT_K + 2)


def test_item_in_both_lists_outranks_a_single_first_place():
    # "b" is second in each list; "a" is first in one and absent from the other.
    # Appearing in both should let b overtake a — the whole point of fusion.
    dense = ["a", "b", "c"]
    sparse = ["d", "b", "e"]
    ranking = [item for item, _ in reciprocal_rank_fusion([dense, sparse])]
    assert ranking[0] == "b"
    assert ranking.index("b") < ranking.index("a")


def test_ties_break_by_first_appearance_deterministically():
    # Two items each appear once at rank 1 in different lists — equal score.
    # First-seen order (across the input lists) decides, so it is stable.
    fused = reciprocal_rank_fusion([["x"], ["y"]])
    assert [item for item, _ in fused] == ["x", "y"]
    # And it is order-independent-of-run: same input, same output.
    assert reciprocal_rank_fusion([["x"], ["y"]]) == fused


def test_k_damps_top_rank_contribution():
    # A larger k flattens the gap between rank 1 and rank 2.
    small = dict(reciprocal_rank_fusion([["a", "b"]], k=1))
    large = dict(reciprocal_rank_fusion([["a", "b"]], k=1000))
    assert (small["a"] - small["b"]) > (large["a"] - large["b"])


def test_empty_input():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
