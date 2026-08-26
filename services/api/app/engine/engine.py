"""The insight engine — orchestrates router -> paths -> synthesis -> envelope.

This is the public entry point: ``InsightEngine.ask(question)`` returns a single
typed :class:`AnswerEnvelope`. It wires the governed semantic layer, the
warehouse executor, the retriever, and the LLM provider together, keeping each
independently testable. See ``docs/05-insight-engine.md``.
"""

from __future__ import annotations

import re

from ..providers.base import Provider
from ..providers.factory import get_provider
from ..semantic.catalog import SemanticCatalog, load_catalog
from ..warehouse.executor import DuckDBWarehouse, Warehouse
from .envelope import AnswerEnvelope, Chart, ChartSeries, Citation, CorrectionAttempt, Table
from .retrieval import FixtureRetriever, Retriever
from .router import route
from .selfcorrect import AbstainSignal, suggest_metrics
from .structured import StructuredResult, run_structured
from .synthesis import synthesize


class InsightEngine:
    def __init__(self, *, catalog: SemanticCatalog, warehouse: Warehouse,
                 retriever: Retriever, provider: Provider, today: str = "2026-07-15"):
        self.catalog = catalog
        self.warehouse = warehouse
        self.retriever = retriever
        self.provider = provider
        self.today = today

    # ---- convenience constructor for the offline fixture stack ---------------
    @classmethod
    def fixture(cls, provider: Provider | None = None, today: str = "2026-07-15") -> InsightEngine:
        catalog = load_catalog()
        return cls(
            catalog=catalog,
            warehouse=DuckDBWarehouse(allow_tables=set(catalog.allow_tables)),
            retriever=FixtureRetriever(),
            provider=provider or get_provider("fake"),
            today=today,
        )

    # ---- main entry point ----------------------------------------------------
    def ask(self, question: str) -> AnswerEnvelope:
        r = route(question, self.catalog, self.provider, self.today)

        if r["route"] == "clarify":
            return AnswerEnvelope(
                answer="I need a bit more detail to answer accurately.",
                route="clarify", confidence="low", clarifying_question=r["clarify"],
            )

        # The question named a metric that is not in the governed catalog and no
        # documents can stand in for it: refuse rather than compute the wrong
        # thing (docs/05 §9). Abstention is distinct from clarification — we
        # understood the question, we just cannot answer it reliably.
        if r.get("metric_unresolved") and not r["needs_docs"]:
            requested = _safe_echo(r.get("requested_metric"))
            return self._abstain(
                f"'{requested}' is not a governed metric, so I cannot compute it "
                "reliably.",
                suggest_metrics(requested, self.catalog),
            )

        structured: StructuredResult | None = None
        attempts: list[CorrectionAttempt] = []
        try:
            if r["route"] in ("structured", "hybrid") and r.get("time_range"):
                structured = run_structured(
                    r, self.catalog, self.warehouse, self.provider, question)
                attempts = structured.attempts
        except AbstainSignal as sig:
            return self._abstain(sig.reason, sig.suggestions, attempts=sig.attempts)

        docs = []
        if r["needs_docs"]:
            query, filters = _retrieval_query(question, r, structured)
            docs = self.retriever.search(query, filters=filters, k=5)

        # Nothing to stand on — no governed number and no supporting document.
        if structured is None and not docs:
            return self._abstain(
                "I couldn't map that question to a governed metric or find any "
                "supporting documents.",
                suggest_metrics(r.get("metric"), self.catalog),
            )

        # A well-formed governed query that genuinely matched no rows. This is an
        # honest empty result, not a refusal and not a fabricated number.
        if structured is not None and structured.status == "no_data":
            return self._no_data_envelope(structured, attempts)

        findings = structured.findings if structured else {"kind": "docs"}
        synth = synthesize(question, findings, docs, self.provider)

        return AnswerEnvelope(
            answer=synth["answer"],
            route=r["route"],
            sql=structured.sql if structured else [],
            tables=structured.tables if structured else [],
            citations=_citations(docs),
            chart=_chart_for(structured.tables, findings) if structured else None,
            confidence=synth.get("confidence", "medium"),
            caveats=synth.get("caveats", []),
            attempts=attempts,
        )

    # ---- abstention + no-data envelopes --------------------------------------
    def _abstain(self, reason: str, suggestions: list[str],
                 attempts: list[CorrectionAttempt] | None = None) -> AnswerEnvelope:
        return AnswerEnvelope(
            answer="I can't answer that reliably, so I won't guess. " + reason,
            route="abstain", confidence="low",
            abstained=True, abstain_reason=reason, suggestions=suggestions,
            attempts=attempts or [],
        )

    def _no_data_envelope(self, structured: StructuredResult,
                          attempts: list[CorrectionAttempt]) -> AnswerEnvelope:
        f = structured.findings
        metric = f.get("metric", "the requested metric")
        period = f.get("period", "that period")
        return AnswerEnvelope(
            answer=(f"There is no {metric} data for {period}. The query was valid "
                    "and executed against the warehouse, but matched no rows — a "
                    "genuine absence of data, not an error and not zero."),
            route="structured", confidence="high",
            sql=structured.sql, tables=structured.tables,
            caveats=["No rows matched a well-formed, governed query."],
            attempts=attempts,
        )


_ECHO_ALLOWED = re.compile(r"[^A-Za-z0-9 _.\-]")


def _safe_echo(value: object, limit: int = 60) -> str:
    """Neutralize attacker-controlled text before quoting it back to the user.

    An abstention names the metric the question asked for, so whatever the user
    (or an upstream router) supplied is reflected into the answer. Nothing
    executes it — but a client rendering the answer as HTML would turn a crafted
    metric name into markup, so the echo is stripped to a safe character set and
    truncated rather than passed through.
    """
    text = "" if value is None else str(value)
    cleaned = _ECHO_ALLOWED.sub("", text).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "..."
    return cleaned or "that metric"


def _retrieval_query(question: str, r: dict, structured) -> tuple[str, dict]:
    """Build the retrieval query + filters.

    For a "why did X change?" question, scope the search to the *top-declining
    segments* the structured path found (doc 05 §3.3): the explanation lives in
    documents about those segments, not in the words of the question. Explicit
    entities in the question always take precedence.
    """
    filters: dict = {}
    if r.get("time_range"):
        filters["date_range"] = r["time_range"]
    for key in ("region", "category"):
        if r.get("entities", {}).get(key):
            filters[key] = r["entities"][key]

    query = question
    findings = structured.findings if structured else None
    if findings and findings.get("kind") == "change":
        extra = []
        tr = findings.get("top_region")
        tc = findings.get("top_category")
        if tr and tr.get("delta", 0) < 0:
            filters.setdefault("region", [tr["region"]])
            extra.append(tr["region"])
        if tc and tc.get("delta", 0) < 0:
            filters.setdefault("category", [tc["category"]])
            extra.append(tc["category"])
        if extra:
            query = f"{question} {' '.join(extra)} complaints issues delays"
    return query, filters


def _citations(docs) -> list[Citation]:
    return [
        Citation(n=i + 1, doc_id=d.doc_id, source_type=d.source_type,
                 title=d.title, date=d.date, score=d.score)
        for i, d in enumerate(docs)
    ]


def _chart_for(tables: list[Table], findings: dict) -> Chart | None:
    if not tables:
        return None
    kind = findings.get("kind")
    metric = findings.get("metric", "value")
    first = tables[0]
    if kind == "change":  # trend table: [period, metric]
        return Chart(type="line", x=first.columns[0],
                     series=[ChartSeries(name=metric, y=first.columns[-1])], data_ref="tables[0]")
    if kind == "grouped":
        return Chart(type="bar", x=first.columns[0],
                     series=[ChartSeries(name=metric, y=first.columns[-1])], data_ref="tables[0]")
    return None
