"""Self-correction + abstention tests for the insight engine (offline).

These exercise the reliability additions from docs/05 §9:
  * a mis-selected governed query is corrected and then answers,
  * a build/guardrail rejection is corrected rather than raising,
  * an unknown metric abstains with a suggestion and never returns a number,
  * a well-formed filter with genuinely no rows reports "no data" (not abstain),
  * a persistently-bad selection abstains after a bounded number of retries,
  * the existing decline answer is unregressed and its numbers come from SQL.

Everything runs on the fixture stack (DuckDB + rules-based provider), so it is
deterministic and needs no external services.
"""

from __future__ import annotations

import json
import re

from app.engine.engine import InsightEngine
from app.engine.selfcorrect import Corrector
from app.providers.base import Provider
from app.providers.fake import FakeProvider, _task_of
from app.semantic.catalog import load_catalog
from app.semantic.query_builder import Filter, MetricSelection
from app.warehouse.executor import DuckDBWarehouse


def make_engine(provider: Provider | None = None) -> InsightEngine:
    return InsightEngine.fixture(provider=provider)


# --- 1. forced mis-selection is corrected by the provider, then answers -------
class LLMCorrectingProvider(FakeProvider):
    """A provider that routes to a mis-selection (units_sold sliced by region —
    not an allowed dimension) and, when asked to correct, returns a valid
    governed selection sliced by an allowed dimension instead."""

    def complete(self, prompt: str, **opts) -> str:
        if _task_of(prompt) == "correct_selection":
            return json.dumps({"metric": "units_sold", "dimensions": ["category"]})
        return super().complete(prompt, **opts)


def test_forced_misselection_is_corrected_and_answers():
    env = make_engine(LLMCorrectingProvider()).ask("units sold by region last quarter")
    assert not env.abstained
    assert env.route == "structured"
    assert env.attempts, "a correction should have been recorded"
    assert env.attempts[0].resolution == "corrected"
    # It actually answered from the corrected (governed) selection.
    assert env.sql
    assert env.tables and env.tables[0].rows
    # The corrected slice is category (region was not allowed for units_sold).
    assert env.tables[0].columns[0] == "category"


# --- 2. a build/guardrail rejection is corrected, never a 500 -----------------
def test_build_rejection_triggers_correction_not_error():
    # Plain fake provider cannot author a correction, so the deterministic repair
    # drops the off-allow-list grouping dimension. The engine must still answer
    # (no exception, no abstain) with a governed number.
    env = make_engine().ask("units sold by region last quarter")
    assert not env.abstained
    assert env.attempts and env.attempts[0].resolution == "corrected"
    assert env.sql
    # Fell back to a scalar reading once the bad dimension was dropped.
    assert env.tables and env.tables[0].rows
    assert isinstance(env.tables[0].rows[0][-1], int | float)


# --- 3. unknown metric abstains with a suggestion and never a number ----------
class UnknownMetricProvider(Provider):
    """Routes to a metric that is not in the governed catalog."""

    name = "fake"

    def complete(self, prompt: str, **opts) -> str:
        return json.dumps({
            "route": "structured",
            "metric": "profit_wizardry",  # not a governed metric
            "time_range": {"start": "2026-04-01", "end": "2026-06-30"},
            "prior_time_range": None,
            "group_dims": [],
            "entities": {},
            "is_change_question": False,
            "needs_docs": False,
            "clarify": None,
        })


def test_unknown_metric_abstains_with_suggestion_and_no_number():
    env = make_engine(UnknownMetricProvider()).ask("What was the profit wizardry last quarter?")
    assert env.route == "abstain"
    assert env.abstained is True
    assert env.abstain_reason
    assert env.suggestions, "abstention must offer a concrete next step"
    assert env.sql == []
    # NEVER a fabricated figure in the refusal.
    assert not re.search(r"\d", env.answer)


# --- 4. well-formed filter with genuinely no rows -> "no data", not abstain ---
class EmptyPeriodProvider(Provider):
    """A valid governed metric over a period the fixture warehouse has no data for."""

    name = "fake"

    def complete(self, prompt: str, **opts) -> str:
        return json.dumps({
            "route": "structured",
            "metric": "revenue",
            "time_range": {"start": "1990-01-01", "end": "1990-03-31"},
            "prior_time_range": None,
            "group_dims": [],
            "entities": {},
            "is_change_question": False,
            "needs_docs": False,
            "clarify": None,
        })


def test_wellformed_filter_no_rows_is_no_data_not_abstain():
    env = make_engine(EmptyPeriodProvider()).ask("What was revenue in early 1990?")
    assert env.abstained is False
    assert env.route == "structured"
    assert "no" in env.answer.lower() and "data" in env.answer.lower()
    # A clear empty result — NOT a fabricated 0-that-looks-real.
    assert env.tables and env.tables[0].rows == []
    assert env.sql, "the (valid) query was still executed and shown"


# --- 5. persistently-bad selection abstains after bounded retries -------------
class StubBadCorrector(Provider):
    """Always proposes the same (still-invalid) corrected selection."""

    name = "fake"

    def complete(self, prompt: str, **opts) -> str:
        return json.dumps({"metric": "still_not_a_metric", "dimensions": []})


def test_bounded_retries_give_up_without_looping():
    catalog = load_catalog()
    corr = Corrector(
        catalog,
        DuckDBWarehouse(allow_tables=set(catalog.allow_tables)),
        StubBadCorrector(),
        max_retries=2,
    )
    bad = MetricSelection(
        metric="also_not_a_metric", dimensions=[],
        filters=[Filter(dimension="date", op="between",
                        values=["2026-04-01", "2026-06-30"])],
    )
    out = corr.execute(bad, stage="scalar:test")
    assert out.status == "failed"
    # Bounded: at most max_retries+1 attempts, and it stopped (did not loop).
    assert 1 <= len(out.attempts) <= 3
    assert out.attempts[-1].resolution == "gave_up"


# --- 6. no regression on the decline answer; numbers come only from SQL -------
def test_decline_question_unregressed_numbers_from_sql():
    env = make_engine().ask("Why did sales decline last quarter?")
    assert env.route == "hybrid"
    assert not env.abstained
    assert env.attempts == [], "the deterministic decline template needs no correction"
    assert "north" in env.answer.lower()
    trend = env.tables[0]
    values = {row[0]: row[1] for row in trend.rows}
    assert values["2026Q2"] < values["2026Q1"]
    assert all(isinstance(r[1], int | float) for r in trend.rows)
