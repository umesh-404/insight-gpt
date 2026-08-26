"""RAG faithfulness harness for the InsightGPT insight engine.

For a set of unstructured / hybrid questions this runs the engine and scores
whether the synthesized answer is *grounded* in the evidence it returned — the
executed tables and the cited documents — rather than fabricated. It ships a
deterministic offline scorer (lexical / claim-in-context heuristics) that runs
with the ``fake`` provider and no models, plus an optional LLM-judge path gated
behind an env flag and a real provider (skipped in CI).

Metrics:

* **groundedness rate** — share of answer sentences whose content is supported
  by the evidence (lexical overlap with cited document text + table cells, or a
  resolved citation marker).
* **citation coverage** — every ``[n]`` marker in an answer resolves to a real
  citation, and every answer that leans on documents carries at least one.
* **no-fabricated-number** — every *absolute* number in the answer appears in a
  returned table cell. Percentages are exempted: they are ratios derived from
  grounded values and are not expected to appear verbatim in a table (a
  documented limitation of the offline check).

The engine's citation objects intentionally do not carry document bodies, so the
harness reconstructs the evidence text the engine saw from the same fixture
corpus, keyed by ``doc_id``.

Run as a script (prints a scoreboard) or as pytest (``test_faithfulness_eval``
asserts the score floors)::

    python tests/eval/faithfulness.py
    pytest tests/eval/faithfulness.py
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import runner  # noqa: E402  (sibling module; path set up on the next line)

runner.bootstrap_paths()

from app.engine.engine import InsightEngine  # noqa: E402
from app.engine.envelope import AnswerEnvelope  # noqa: E402
from app.fixtures.retail import get_sample_documents  # noqa: E402

FLOORS = {
    "groundedness_rate": 0.80,
    "citation_coverage": 0.90,
    "no_fabricated_number_rate": 0.90,
}

# A sentence counts as grounded when at least this share of its content tokens
# appear in the evidence context (or it carries a resolved citation marker).
GROUNDING_THRESHOLD = 0.5

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "from", "by",
    "was", "were", "is", "are", "this", "that", "these", "those", "with", "at",
    "as", "it", "its", "we", "our", "their", "they", "them", "his", "her",
    "mainly", "driven", "change", "period", "attribute", "concern", "items",
    "representative", "main", "themes", "no", "found", "relevant", "documents",
    "document", "there", "about", "than", "into", "out", "up", "down",
}

QUESTIONS = [
    "Why did revenue decline last quarter?",
    "Why did sales decline last quarter?",
    "Summarize customer complaints this month.",
    "What are customers saying about deliveries?",
    "Summarize the reviews about electronics this quarter.",
]


# --------------------------------------------------------------------------- #
# text helpers                                                                #
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[a-z0-9]+")
# A number token not glued to letters (so "2026Q2", "W05" are not treated as
# free-standing quantities). Group 1 is the number; a trailing '%' marks it as a
# percentage. The trailing boundary is what keeps "2026" out of "2026Q2".
_NUMBER = re.compile(r"(?<![A-Za-z0-9])(\d[\d,]*(?:\.\d+)?)(%?)(?![A-Za-z0-9])")
_MARKER = re.compile(r"\[\d+\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _content_tokens(text: str) -> set[str]:
    # Pure-digit fragments (e.g. "300", "000" split out of "1,300,000") carry no
    # grounding signal and are handled by the separate number check, so drop them
    # here and keep only word/label tokens.
    return {
        w for w in _WORD.findall(text.lower())
        if len(w) > 2 and w not in _STOP and not w.isdigit()
    }


def _norm_number(raw: str) -> str:
    raw = raw.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value.is_integer():
        return str(abs(int(value)))
    return str(abs(value))


def _answer_numbers(answer: str) -> list[str]:
    """Absolute numbers in the answer (percentages and [n] markers excluded)."""
    answer = _MARKER.sub(" ", answer)  # citation markers are not quantities
    numbers = []
    for match in _NUMBER.finditer(answer):
        if match.group(2) == "%":
            continue
        numbers.append(_norm_number(match.group(1)))
    return numbers


def _table_numbers(env: AnswerEnvelope) -> set[str]:
    grounded: set[str] = set()
    for table in env.tables:
        for row in table.rows:
            for cell in row:
                if isinstance(cell, bool):
                    continue
                if isinstance(cell, (int, float)):
                    grounded.add(_norm_number(str(cell)))
    return grounded


# --------------------------------------------------------------------------- #
# evidence reconstruction                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class DocIndex:
    by_id: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def build(cls) -> DocIndex:
        return cls(by_id={d["doc_id"]: d for d in get_sample_documents()})

    def context_tokens(self, env: AnswerEnvelope) -> set[str]:
        tokens: set[str] = set()
        for table in env.tables:
            tokens |= _content_tokens(" ".join(table.columns))
            for row in table.rows:
                tokens |= _content_tokens(" ".join(str(c) for c in row))
        for cite in env.citations:
            tokens |= _content_tokens(cite.title)
            doc = self.by_id.get(cite.doc_id)
            if doc:
                tokens |= _content_tokens(f"{doc['title']} {doc['body']}")
        return tokens


# --------------------------------------------------------------------------- #
# per-answer scoring                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class AnswerScore:
    question: str
    sentences: int
    grounded_sentences: int
    markers: int
    resolved_markers: int
    numbers: int
    grounded_numbers: int
    has_citation: bool
    needs_citation: bool

    @property
    def groundedness(self) -> float:
        return 1.0 if self.sentences == 0 else self.grounded_sentences / self.sentences

    @property
    def numbers_ok(self) -> bool:
        return self.numbers == self.grounded_numbers


def score_answer(question: str, env: AnswerEnvelope, docs: DocIndex) -> AnswerScore:
    context = docs.context_tokens(env)
    valid_markers = {c.n for c in env.citations}
    table_numbers = _table_numbers(env)

    sentences = [s for s in _SENTENCE.split(env.answer.strip()) if s.strip()]
    grounded = 0
    markers = resolved = 0
    for sentence in sentences:
        sentence_markers = [int(n) for n in re.findall(r"\[(\d+)\]", sentence)]
        markers += len(sentence_markers)
        resolved += sum(1 for n in sentence_markers if n in valid_markers)

        tokens = _content_tokens(sentence)
        overlap = len(tokens & context)
        ratio = overlap / max(1, len(tokens))
        has_resolved = any(n in valid_markers for n in sentence_markers)
        if ratio >= GROUNDING_THRESHOLD or has_resolved:
            grounded += 1

    numbers = _answer_numbers(env.answer)
    grounded_numbers = sum(1 for n in numbers if n in table_numbers)
    needs_citation = env.route in ("unstructured", "hybrid")

    return AnswerScore(
        question=question,
        sentences=len(sentences),
        grounded_sentences=grounded,
        markers=markers,
        resolved_markers=resolved,
        numbers=len(numbers),
        grounded_numbers=grounded_numbers,
        has_citation=bool(env.citations),
        needs_citation=needs_citation,
    )


# --------------------------------------------------------------------------- #
# aggregate                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class Report:
    groundedness_rate: float
    citation_coverage: float
    no_fabricated_number_rate: float
    rows: list[list[object]]
    detail: dict[str, str]


def run_eval(engine: InsightEngine | None = None) -> Report:
    engine = engine or InsightEngine.fixture()
    docs = DocIndex.build()

    scores = [score_answer(q, engine.ask(q), docs) for q in QUESTIONS]

    total_sentences = sum(s.sentences for s in scores)
    grounded_sentences = sum(s.grounded_sentences for s in scores)
    total_markers = sum(s.markers for s in scores)
    resolved_markers = sum(s.resolved_markers for s in scores)
    needing = [s for s in scores if s.needs_citation]
    with_citation = sum(1 for s in needing if s.has_citation)

    # Citation coverage combines two failure modes: an unresolved [n] marker, and
    # a documents-based answer that carries no citation at all.
    marker_component = 1.0 if total_markers == 0 else resolved_markers / total_markers
    presence_component = 1.0 if not needing else with_citation / len(needing)
    citation_coverage = min(marker_component, presence_component)

    total_numbers = sum(s.numbers for s in scores)
    grounded_numbers = sum(s.grounded_numbers for s in scores)
    answers_numbers_ok = sum(1 for s in scores if s.numbers_ok)

    rows = [
        [
            _short(s.question), s.sentences,
            f"{s.grounded_sentences}/{s.sentences}",
            f"{s.resolved_markers}/{s.markers}",
            f"{s.grounded_numbers}/{s.numbers}",
            "yes" if s.has_citation else ("MISSING" if s.needs_citation else "n/a"),
        ]
        for s in scores
    ]

    return Report(
        groundedness_rate=1.0 if total_sentences == 0 else grounded_sentences / total_sentences,
        citation_coverage=citation_coverage,
        no_fabricated_number_rate=answers_numbers_ok / len(scores),
        rows=rows,
        detail={
            "sentences": f"{grounded_sentences}/{total_sentences}",
            "markers": f"{resolved_markers}/{total_markers}",
            "numbers": f"{grounded_numbers}/{total_numbers}",
            "answers_clean": f"{answers_numbers_ok}/{len(scores)}",
            "citation_presence": f"{with_citation}/{len(needing)}",
        },
    )


def _short(text: str, width: int = 44) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def print_report(report: Report) -> None:
    runner.print_scoreboard(
        "RAG faithfulness (fixture stack, fake provider)",
        ["question", "sents", "grounded", "cites", "numbers", "has_cite"],
        report.rows,
    )
    runner.print_metrics(
        "Faithfulness scores",
        {
            "groundedness_rate": report.groundedness_rate,
            "citation_coverage": report.citation_coverage,
            "no_fabricated_number_rate": report.no_fabricated_number_rate,
        },
        FLOORS,
    )
    print(
        f"\ndetail: grounded sentences {report.detail['sentences']}, "
        f"resolved markers {report.detail['markers']}, "
        f"grounded numbers {report.detail['numbers']}, "
        f"clean answers {report.detail['answers_clean']}, "
        f"citation presence {report.detail['citation_presence']}."
    )
    if os.getenv("FAITHFULNESS_LLM_JUDGE") == "1":
        _run_llm_judge()
    runner.emit_results_json("faithfulness", {
        "groundedness_rate": round(report.groundedness_rate, 4),
        "citation_coverage": round(report.citation_coverage, 4),
        "no_fabricated_number_rate": round(report.no_fabricated_number_rate, 4),
        "detail": report.detail,
    })


# --------------------------------------------------------------------------- #
# optional LLM-judge path (gated; never runs in CI)                          #
# --------------------------------------------------------------------------- #
def _run_llm_judge() -> None:
    """Second opinion from a real provider. Requires FAITHFULNESS_LLM_JUDGE=1 and
    a configured non-fake provider (LLM_PROVIDER + its key). Skipped otherwise."""
    from app.providers.base import Message, extract_json
    from app.providers.factory import get_provider

    provider_name = os.getenv("LLM_PROVIDER", "ollama")
    if provider_name == "fake":
        print("LLM judge requested but LLM_PROVIDER=fake — nothing to judge; skipping.")
        return
    provider = get_provider(provider_name)
    engine = InsightEngine.fixture(provider=provider)
    print(f"\nLLM judge ({provider_name}):")
    for question in QUESTIONS:
        env = engine.ask(question)
        prompt = (
            "You are grading whether an answer is grounded in its evidence. "
            "Respond with JSON {\"grounded\": true|false, \"reason\": str}.\n"
            f"QUESTION: {question}\nANSWER: {env.answer}\n"
            f"CITATIONS: {[c.doc_id for c in env.citations]}\n"
        )
        try:
            verdict = extract_json(provider.chat([Message(role="user", content=prompt)]))
        except Exception as exc:  # noqa: BLE001 - a judge failure must not crash the report
            print(f"  - {question[:50]}: judge error ({exc})")
            continue
        print(f"  - {question[:50]}: grounded={verdict.get('grounded')}")


# --------------------------------------------------------------------------- #
# pytest entry point                                                          #
# --------------------------------------------------------------------------- #
def test_faithfulness_eval() -> None:
    report = run_eval()
    assert report.groundedness_rate >= FLOORS["groundedness_rate"], (
        f"groundedness {report.groundedness_rate:.3f} below floor "
        f"{FLOORS['groundedness_rate']} ({report.detail['sentences']})"
    )
    assert report.citation_coverage >= FLOORS["citation_coverage"], (
        f"citation coverage {report.citation_coverage:.3f} below floor "
        f"{FLOORS['citation_coverage']} (markers {report.detail['markers']}, "
        f"presence {report.detail['citation_presence']})"
    )
    assert report.no_fabricated_number_rate >= FLOORS["no_fabricated_number_rate"], (
        f"no-fabricated-number rate {report.no_fabricated_number_rate:.3f} below floor "
        f"{FLOORS['no_fabricated_number_rate']} ({report.detail['numbers']})"
    )


if __name__ == "__main__":
    print_report(run_eval())
