"""Sparse tokenizer: determinism, identifier/SKU splitting, weighting, stopwords."""

from __future__ import annotations

from retrieval.config import SparseConfig
from retrieval.sparse import build_sparse_vector

CFG = SparseConfig(enabled=True, split_identifiers=True, min_token_len=2, max_tokens_per_chunk=400)


def test_deterministic_across_calls():
    a = build_sparse_vector("North fulfilment backlog", CFG)
    b = build_sparse_vector("North fulfilment backlog", CFG)
    assert a == b
    assert a[0]  # non-empty


def test_indices_are_uint32_range():
    indices, values = build_sparse_vector("electronics order ORD-88213", CFG)
    assert all(0 <= i <= 0x7FFFFFFF for i in indices)
    assert len(indices) == len(values)


def test_empty_vocabulary_returns_empty():
    # All stopwords / too short -> empty vector (the degrade-to-dense case).
    assert build_sparse_vector("the a an of to", CFG) == ([], [])
    assert build_sparse_vector("???", CFG) == ([], [])


def test_camel_and_sku_splitting_adds_subtokens():
    whole = build_sparse_vector("X230", CFG)
    # "X230" splits into X / 230 as half-weight subtokens plus the whole token.
    indices, values = whole
    assert len(indices) >= 2
    # The full token outweighs any single subtoken (1.0 vs 0.5).
    assert max(values) >= 1.0
    assert min(values) == 0.5


def test_split_can_be_disabled():
    off = SparseConfig(
        enabled=True, split_identifiers=False, min_token_len=2, max_tokens_per_chunk=400
    )
    indices, values = build_sparse_vector("UserRepository", off)
    assert len(indices) == 1  # only the whole token, no camel parts
    assert values == [1.0]


def test_repeated_term_increments_frequency():
    indices, values = build_sparse_vector("backlog backlog backlog", CFG)
    assert indices  # one term
    assert max(values) >= 3.0


def test_query_matches_index_tokens():
    # The query builder must produce the same ids as the indexed text for the
    # shared terms — this is what makes lexical matching work at all.
    doc = dict(zip(*build_sparse_vector("North fulfilment centre backlog", CFG), strict=True))
    qry = dict(zip(*build_sparse_vector("backlog", CFG), strict=True))
    assert set(qry).issubset(set(doc))
