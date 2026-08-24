"""Synthesis: turn findings + evidence into explainable prose.

The final LLM call. It receives authoritative numeric findings and quoted
document evidence and writes the narrative — it never produces numbers itself
(those are passed through from SQL) and treats document text as untrusted quoted
evidence, never instructions. See ``docs/05-insight-engine.md`` §6 and §8.
"""

from __future__ import annotations

from ..providers.base import Provider, extract_json
from .prompts import synth_prompt
from .retrieval import RetrievedDoc


def synthesize(question: str, findings: dict, docs: list[RetrievedDoc],
               provider: Provider) -> dict:
    evidence = [
        {"n": i + 1, "doc_id": d.doc_id, "source_type": d.source_type,
         "title": d.title, "body": d.body, "date": d.date, "score": d.score}
        for i, d in enumerate(docs)
    ]
    prompt = synth_prompt(question, findings, evidence)
    raw = provider.complete(prompt, json=True, temperature=0.0)
    try:
        obj = extract_json(raw)
    except ValueError:
        # Degrade honestly rather than fabricate (docs/05 §9).
        obj = {"answer": raw.strip()[:600] or "Unable to synthesize an answer.",
               "confidence": "low", "caveats": ["Synthesis output was not structured."]}
    obj.setdefault("confidence", "medium")
    obj.setdefault("caveats", [])
    return obj
