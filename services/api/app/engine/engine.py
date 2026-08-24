"""The insight engine — orchestrates router -> paths -> synthesis -> envelope.

This is the public entry point: ``InsightEngine.ask(question)`` returns a single
typed :class:`AnswerEnvelope`. It wires the governed semantic layer, the
warehouse executor, the retriever, and the LLM provider together, keeping each
independently testable. See ``docs/05-insight-engine.md``.
"""

from __future__ import annotations

from ..providers.base import Provider
from ..providers.factory import get_provider
from ..semantic.catalog import SemanticCatalog, load_catalog
from ..warehouse.executor import DuckDBWarehouse, Warehouse
from .envelope import AnswerEnvelope, Chart, ChartSeries, Citation, Table
from .retrieval import FixtureRetriever, Retriever
from .router import route
from .structured import run_structured
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

        structured = None
        if r["route"] in ("structured", "hybrid") and r.get("time_range"):
            structured = run_structured(r, self.catalog, self.warehouse)

        docs = []
        if r["needs_docs"]:
            query, filters = _retrieval_query(question, r, structured)
            docs = self.retriever.search(query, filters=filters, k=5)

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
        )


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
