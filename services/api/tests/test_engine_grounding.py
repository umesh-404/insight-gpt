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

from app.engine.engine import InsightEngine
from app.providers.fake import FakeProvider, _task_of

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
class _FlipFlopProvider(FakeProvider):
    """A model whose breakdown dimension is unstable across identical calls.

    Every sampling provider behaves like this to some degree; this one just
    does it on every other call so the property is testable. The engine must
    not let that instability reach the envelope, because a question answered
    with one figure and then with a twelve-row table is a question the user
    cannot trust - and a demo that contradicts itself on a re-run.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    def complete(self, prompt: str, **opts) -> str:
        raw = super().complete(prompt, **opts)
        if _task_of(prompt) != "route":
            return raw
        self.calls += 1
        obj = json.loads(raw)
        obj["group_dims"] = ["date"] if self.calls % 2 else []
        return json.dumps(obj)


def test_unstable_dimension_choice_does_not_change_the_envelope():
    question = "what was revenue last quarter"
    envelopes = [
        _engine(_FlipFlopProvider()).ask(question).model_dump() for _ in range(4)
    ]
    # Two of those four calls saw the model answer "date" instead of "none".
    first = envelopes[0]
    for other in envelopes[1:]:
        assert other == first, "identical input must produce an identical envelope"
    assert first["chart"] is None  # a scalar question, not a breakdown


def test_an_explicit_breakdown_is_still_honoured():
    env = _engine(_FlipFlopProvider()).ask("what was revenue by month last quarter")
    assert env.chart is not None
    assert env.tables[0].title.endswith("by date")
