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
    # The date bound is nested in a `should` so an uploaded dataset can be
    # admitted beside it (see the next test); the bound itself is unchanged.
    date_cond = f.must[0].should[0]
    assert date_cond.key == "created_at"
    # qdrant-client parses the ISO strings into datetimes; check the bounds land.
    assert date_cond.range.gte is not None and date_cond.range.lte is not None
    assert str(date_cond.range.gte).startswith("2026-08-01")
    assert str(date_cond.range.lte).startswith("2026-08-31")


def test_a_date_window_does_not_exclude_uploaded_datasets():
    """The bug that made the upload feature retrieve nothing, silently.

    A corpus document's `created_at` is when the ticket was raised - a content
    date, so bounding it by the question's period is right. An upload's is
    when the file was handed over, which says nothing about the period it
    covers. The router attaches a resolved window to every document search, so
    a hard bound on that field removed every uploaded chunk from every
    question.
    """
    f = build_filter({"date_range": {"start": "2026-08-01", "end": "2026-08-31"}})
    branches = f.must[0].should
    assert len(branches) == 2, "the date bound must have an escape hatch"
    exemption = branches[1]
    assert exemption.key == "source_type"
    assert exemption.match.value == "dataset"


def test_multiple_conditions_combined_under_must():
    f = build_filter({"source_type": "review", "region": ["North"]})
    assert len(f.must) == 2
