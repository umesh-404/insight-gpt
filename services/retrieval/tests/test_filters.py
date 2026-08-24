"""Metadata filter construction (pure — builds Qdrant models, no server call)."""

from __future__ import annotations

from qdrant_client import models

from retrieval.search import build_filter


def test_none_and_empty_yield_no_filter():
    assert build_filter(None) is None
    assert build_filter({}) is None
    assert build_filter({"region": None}) is None


def test_scalar_becomes_match_value():
    f = build_filter({"source_type": "ticket"})
    assert isinstance(f, models.Filter)
    cond = f.must[0]
    assert cond.key == "source_type"
    assert cond.match.value == "ticket"


def test_list_becomes_match_any():
    f = build_filter({"region": ["North", "South"]})
    cond = f.must[0]
    assert cond.match.any == ["North", "South"]


def test_date_range_becomes_created_at_range():
    f = build_filter({"date_range": {"start": "2026-08-01", "end": "2026-08-31"}})
    cond = f.must[0]
    assert cond.key == "created_at"
    # qdrant-client parses the ISO strings into datetimes; check the bounds land.
    assert cond.range.gte is not None and cond.range.lte is not None
    assert str(cond.range.gte).startswith("2026-08-01")
    assert str(cond.range.lte).startswith("2026-08-31")


def test_multiple_conditions_combined_under_must():
    f = build_filter({"source_type": "review", "region": ["North"]})
    assert len(f.must) == 2
