"""Deterministic point ids: stable, uuid5, unique per (doc, chunk)."""

from __future__ import annotations

import uuid

from retrieval.ids import NAMESPACE, point_id


def test_deterministic_across_calls():
    assert point_id("TICKET-40122", 0) == point_id("TICKET-40122", 0)


def test_distinct_chunks_distinct_ids():
    assert point_id("TICKET-40122", 0) != point_id("TICKET-40122", 1)


def test_distinct_docs_distinct_ids():
    assert point_id("A", 0) != point_id("B", 0)


def test_is_a_uuid5_of_the_expected_key():
    expected = str(uuid.uuid5(NAMESPACE, "REPORT-Q2-OPS:2"))
    assert point_id("REPORT-Q2-OPS", 2) == expected
    # Parses as a valid UUID.
    assert uuid.UUID(point_id("REPORT-Q2-OPS", 2)).version == 5


def test_namespace_is_pinned():
    # A guard: changing this constant orphans every existing point, so the test
    # exists to make such a change a deliberate, visible failure.
    assert str(NAMESPACE) == "b6a1f2c4-7d3e-5a90-9c21-4e8f0d6b3a17"
