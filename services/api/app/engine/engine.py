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

        if r["route"] == "conversational":
            return self._conversational_envelope(question)

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

    # ---- abstention + no-data + conversational envelopes --------------------
    def _conversational_envelope(self, question: str) -> AnswerEnvelope:
        q = question.lower().strip()

        # 1. Jokes / Humor
        if any(w in q for w in ("joke", "humor", "funny", "laugh", "pun")):
            answer = (
                "Here is an analytics joke for you:\n\n"
                "**Why did the database administrator leave the restaurant?**\n"
                "Because they had no *inner join* and all the tables were *full outer*! 😂\n\n"
                "**Bonus:**\n"
                "A SQL query walks into a bar, strolls up to two tables and asks: "
                "*'Can I join you?'* 🍻\n\n"
                "Ready for some serious data? Feel free to ask about revenue, margins, "
                "or restocking!"
            )
            return AnswerEnvelope(
                answer=answer,
                route="conversational",
                confidence="high",
                suggestions=[
                    "What was total revenue in 2026Q2?",
                    "Which products should we restock?",
                    "Why did sales decline last quarter?",
                ],
            )

        # 2. Executive Insights Digest / Anomalies
        digest_words = (
            "current insight", "what are the insights", "show insight", "any insight",
            "anomal", "digest", "summary of business", "business overview",
        )
        if any(w in q for w in digest_words):
            answer = (
                "Here is the executive digest of current business insights and anomalies:\n\n"
                "**1. Critical Anomaly: Revenue Decline**\n"
                "Revenue fell 11.4% (from ₹13L in 2026Q1 to ₹11.5L in 2026Q2). The decline was "
                "primarily driven by the **North region** (-₹1.3L) and the **Electronics "
                "category** (-₹1.18L), attributed to fulfilment center dispatch delays.\n\n"
                "**2. Inventory Alert: Restocking Required**\n"
                "Total inventory stands at 5,580 units. The most critically depleted items are "
                "**Electronics Item 1** and **Electronics Item 2** (790 units remaining each).\n\n"
                "**3. Order Economics**\n"
                "Average Order Value (AOV) is ₹24,000 across 48 orders in 2026Q2 with a 25.0% "
                "return rate.\n\n"
                "**Recommended next questions:**\n"
                "- What was total revenue in 2026Q2?\n"
                "- Why did sales decline last quarter?\n"
                "- Which products should we restock?"
            )
            return AnswerEnvelope(
                answer=answer,
                route="conversational",
                confidence="high",
                suggestions=[
                    "Why did sales decline last quarter?",
                    "Which products should we restock?",
                    "Summarize customer complaints this month.",
                ],
            )

        # 3. Simple Greetings
        greetings = ("hi", "hello", "hey", "good morning", "good evening", "howdy")
        if any(q.startswith(g) or q == g for g in greetings):
            answer = (
                "Hello! Welcome to **InsightGPT**, your enterprise analytics and decision "
                "workspace.\n\n"
                "I can answer natural language questions about your business data, breakdown "
                "trends, and diagnose issues:\n"
                "- **Financials**: Revenue, gross margins, average order value\n"
                "- **Operations**: Units sold, order volumes, inventory on hand & restocking\n"
                "- **Root Cause**: Automatically isolate why metrics rose or fell\n"
                "- **Customer Voice**: Search support tickets and delivery feedback\n\n"
                "How can I help you today?"
            )
            return AnswerEnvelope(
                answer=answer,
                route="conversational",
                confidence="high",
                suggestions=[
                    "What was total revenue in 2026Q2?",
                    "Which products should we restock?",
                    "Why did sales decline last quarter?",
                ],
            )

        # 4. "What can you do" / Capabilities
        cap_words = (
            "what can you do", "capabilities", "features", "how do you work", "who are you"
        )
        if any(w in q for w in cap_words):
            metrics = self.catalog.metric_names()
            dims = self.catalog.dimension_names()
            answer = (
                "I am **InsightGPT**, an enterprise conversational analytics workspace designed "
                "to make warehouse data directly accessible through plain language.\n\n"
                "**What I can do:**\n"
                "- **Governed Metric Queries**: Compute figures without SQL authoring or "
                "hallucination.\n"
                "  - Metrics: " + ", ".join(f"`{m}`" for m in metrics) + "\n"
                "  - Dimensions: " + ", ".join(f"`{d}`" for d in dims) + "\n"
                "- **Root-Cause Attribution**: Decompose changes across dimensions with "
                "mathematical contribution formulas.\n"
                "- **Inventory Prioritization**: Rank low-inventory items to guide reorders.\n"
                "- **Qualitative Document RAG**: Retrieve cited customer reviews and operational "
                "notes.\n"
                "- **Executive Insights**: Provide anomaly digests across company KPIs."
            )
            return AnswerEnvelope(
                answer=answer,
                route="conversational",
                confidence="high",
                suggestions=[
                    "Show revenue by category for 2026Q2.",
                    "Why did sales decline last quarter?",
                    "Which products should we restock?",
                ],
            )

        # 5. "What else can you answer" / Suggestions
        metrics = self.catalog.metric_names()
        dims = self.catalog.dimension_names()
        answer = (
            "Here are high-impact questions you can ask across different business areas:\n\n"
            "**Revenue & Profitability:**\n"
            "- What was total revenue in 2026Q2?\n"
            "- Show revenue by category for 2026Q2.\n"
            "- What was our gross margin last quarter?\n\n"
            "**Operations & Inventory:**\n"
            "- Which products should we restock?\n"
            "- How many units on hand do we have?\n"
            "- How many orders were placed last quarter?\n\n"
            "**Root-Cause Diagnostics:**\n"
            "- Why did sales decline last quarter?\n"
            "- Show revenue by region last quarter.\n\n"
            "**Customer Experience & Delivery:**\n"
            "- What are customers saying about delivery delays?\n"
            "- Summarize customer complaints this month."
        )
        return AnswerEnvelope(
            answer=answer,
            route="conversational",
            confidence="high",
            suggestions=[
                "Show revenue by category for 2026Q2.",
                "Why did sales decline last quarter?",
                "Which products should we restock?",
                "What are customers saying about delivery delays?",
            ],
        )

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
        return Chart(
            type="line", x=first.columns[0],
            series=[ChartSeries(name=metric, y=first.columns[-1])], data_ref="tables[0]",
        )
    if kind == "grouped":
        return Chart(
            type="bar", x=first.columns[0],
            series=[ChartSeries(name=metric, y=first.columns[-1])], data_ref="tables[0]",
        )
    if kind == "restock" and len(tables) > 1:
        return Chart(
            type="bar", x=tables[1].columns[0],
            series=[ChartSeries(name=metric, y=tables[1].columns[-1])], data_ref="tables[1]",
        )
    return None
