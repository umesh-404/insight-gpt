"""Regression tests for engine-enforced grounding and envelope determinism.

Two failure modes live here, both caused by the engine *trusting* a provider:

* A provider that must choose from a closed enum of governed metrics always
  returns one, so an out-of-domain question ("average employee salary") came
  back as a confident revenue figure. Abstention had been implemented inside
  one provider only, so it did not fire for the configured one.
* An unstable dimension choice from a remote model made the headline question
  return either a scalar or a per-period breakdown at random.

Both are tested through stubs, so they hold for *any* provider.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.engine.engine import InsightEngine
from app.providers.fake import FakeProvider, _task_of
from app.providers.typesafe_provider import TypeSafeProvider

PERIOD = {"start": "2026-04-01", "end": "2026-06-30"}


class ClosedEnumProvider(FakeProvider):
    """A provider that always picks a governed metric and always has a period.

    This is the shape of any hosted model answering a closed-enum question: it
    cannot return "none of the above" reliably, so the engine must not treat the
    selection as evidence that the question is answerable.
    """

    name = "closed-enum"

    def __init__(self, metric: str = "revenue", group_dims: list[str] | None = None):
        self.metric = metric
        self.group_dims = group_dims or []

    def complete(self, prompt: str, **opts) -> str:
        if _task_of(prompt) == "route":
            return json.dumps({
                "route": "structured",
                "metric": self.metric,
                "time_range": dict(PERIOD),
                "prior_time_range": None,
                "group_dims": list(self.group_dims),
                "entities": {},
                "is_change_question": False,
                "needs_docs": False,
                "clarify": None,
            })
        return super().complete(prompt, **opts)


def _engine(provider) -> InsightEngine:
    return InsightEngine.fixture(provider=provider)


def test_out_of_domain_question_abstains_despite_a_governed_metric_choice():
    env = _engine(ClosedEnumProvider()).ask(
        "what is the average employee salary by department"
    )
    assert env.abstained is True
    assert env.route == "abstain"
    assert env.confidence == "low"
    assert "1.3" not in env.answer  # no revenue figure smuggled into the refusal
    assert env.suggestions


def test_time_word_alone_is_not_analytics_intent():
    """The dangerous variant: a year makes the wrong answer look researched."""
    env = _engine(ClosedEnumProvider()).ask("how many support staff did we hire in 2025")
    assert env.abstained is True


def test_question_outside_the_data_range_never_answers_a_different_period():
    env = _engine(ClosedEnumProvider()).ask("what was revenue in 1998")
    # Either an abstention or an honest empty result — but never a figure for a
    # period the question did not ask about.
    assert "2026" not in env.answer
    assert env.abstained or "no revenue data" in env.answer.lower()
    assert "1998" in env.answer


def test_grounded_questions_still_answer():
    """Guardrail against over-aggression: the demo questions must still work."""
    env = _engine(ClosedEnumProvider()).ask("what was revenue last quarter")
    assert not env.abstained
    assert env.route == "structured"
    assert env.tables[0].rows[0][0] > 0

    env = _engine(ClosedEnumProvider()).ask("how did we do last quarter")
    assert not env.abstained


# --- determinism ------------------------------------------------------------
class _FlipFlopClient:
    """A remote model whose dimension choice is unstable across identical calls."""

    def __init__(self):
        self.calls = 0

    def system_one(self, *, state, questions, model=None, **kw):
        self.calls += 1
        dim = "date" if self.calls % 2 else "none"
        return SimpleNamespace(
            choices={
                "route": SimpleNamespace(choice="structured"),
                "metric": SimpleNamespace(choice="revenue"),
                "dimension": SimpleNamespace(choice=dim),
            },
            nouls={"is_change": SimpleNamespace(noul=0.0)},
        )


def _typesafe_with_stub() -> TypeSafeProvider:
    provider = TypeSafeProvider(api_key="")
    provider._client = _FlipFlopClient()
    return provider


def test_unstable_dimension_choice_does_not_change_the_envelope():
    question = "what was revenue last quarter"
    envelopes = [
        _engine(_typesafe_with_stub()).ask(question).model_dump() for _ in range(4)
    ]
    # Two of those four calls saw the model answer "date" instead of "none".
    first = envelopes[0]
    for other in envelopes[1:]:
        assert other == first, "identical input must produce an identical envelope"
    assert first["chart"] is None  # a scalar question, not a breakdown


def test_an_explicit_breakdown_is_still_honoured():
    env = _engine(_typesafe_with_stub()).ask("what was revenue by month last quarter")
    assert env.chart is not None
    assert env.tables[0].title.endswith("by date")
