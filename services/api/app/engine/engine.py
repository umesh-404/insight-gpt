"""The insight engine — orchestrates router -> paths -> synthesis -> envelope.

This is the public entry point: ``InsightEngine.ask(question)`` returns a single
typed :class:`AnswerEnvelope`. It wires the governed semantic layer, the
warehouse executor, the retriever, and the LLM provider together, keeping each
independently testable. See ``docs/05-insight-engine.md``.
"""

from __future__ import annotations

import re

from ..formatting import format_value
from ..providers.base import Provider
from ..providers.factory import get_provider
from ..semantic.catalog import SemanticCatalog, load_catalog
from ..semantic.query_builder import MetricSelection, build_query
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
    # ---- conversational envelope (dynamic live responses) -------------------
    def _conversational_envelope(self, question: str) -> AnswerEnvelope:
        q = question.lower().strip()

        # 1. Jokes / Humor
        if any(w in q for w in ("joke", "humor", "funny", "laugh", "pun")):
            return self._dynamic_humor_envelope(q)

        # 2. Executive Insights Digest / Anomalies
        digest_words = (
            "current insight", "what are the insights", "show insight", "any insight",
            "anomal", "digest", "summary of business", "business overview",
            "business summary", "kpi digest", "executive summary", "executive digest",
        )
        if any(w in q for w in digest_words):
            return self._dynamic_executive_digest_envelope()

        # 3. Simple Greetings
        greetings = (
            "hi", "hello", "hey", "good morning", "good evening", "good afternoon", "howdy",
        )
        if any(q.startswith(g) or q == g for g in greetings):
            return self._dynamic_greeting_envelope(q)

        # 4. "What can you do" / Capabilities
        cap_words = (
            "what can you do", "capabilities", "features", "how do you work", "who are you",
            "what do you do",
        )
        if any(w in q for w in cap_words):
            return self._dynamic_capabilities_envelope()

        # 5. "What else can you answer" / Suggestions
        return self._dynamic_suggestions_envelope()

    def _dynamic_executive_digest_envelope(self) -> AnswerEnvelope:
        from ..insights.detector import detect_insights

        lines = ["Here is the executive digest of current business insights and anomalies:\n"]
        suggestions: list[str] = []

        # 1. Governed Anomaly Detection
        try:
            insights = detect_insights(self)
        except Exception:
            insights = []

        if insights:
            lines.append("### Governed Anomaly Detection")
            for i, ins in enumerate(insights[:2], 1):
                icon = "🔻" if ins.direction == "down" else "🔺"
                lines.append(f"**{i}. {ins.metric_label} ({ins.direction.upper()})** {icon}")
                lines.append(f"{ins.headline}")
                if ins.root_cause:
                    delta_str = format_value(ins.root_cause.delta, ins.metric_format)
                    lines.append(
                        f"- **Root Cause**: Variance concentrated in **{ins.root_cause.segment}** "
                        f"({ins.root_cause.dimension}) with a delta of {delta_str} "
                        f"({abs(ins.root_cause.contribution_pct):.0f}% contribution)."
                    )
                if ins.evidence:
                    doc = ins.evidence[0]
                    lines.append(f"- **Document Grounding**: *\"{doc.title}\"* — {doc.snippet}")
                lines.append("")
                if i == 1:
                    if ins.direction == "down":
                        suggestions.append(
                            f"Why did {ins.metric_label.lower()} decline last quarter?"
                        )
                    else:
                        suggestions.append(f"Show {ins.metric_label.lower()} trend.")
        else:
            lines.append("### Governed Anomaly Detection\nAll governed metrics are stable.\n")

        # 2. Live Inventory Telemetry
        try:
            sel = MetricSelection(
                metric="units_on_hand", dimensions=["product"],
                order_by_metric="asc", limit=3,
            )
            built = build_query(sel, self.catalog)
            res = self.warehouse.run(built.sql, built.params)

            tot_sel = MetricSelection(metric="units_on_hand")
            tot_built = build_query(tot_sel, self.catalog)
            tot_res = self.warehouse.run(tot_built.sql, tot_built.params)
            total_stock = tot_res.rows[0][0] if tot_res.rows else None

            if res.rows:
                lines.append("### Inventory & Restocking Alert")
                if total_stock is not None:
                    lines.append(
                        f"Total inventory on hand: "
                        f"**{format_value(total_stock, 'integer')} units**."
                    )
                lines.append("Critically depleted items requiring replenishment:")
                for prod, count in res.rows:
                    lines.append(f"- **{prod}**: {format_value(count, 'integer')} units remaining")
                lines.append("")
                suggestions.append("Which products should we restock?")
        except Exception:
            pass

        # 3. Live Operating Economics
        econ_items: list[str] = []
        for m_name in ("orders", "avg_order_value", "return_rate"):
            if m_name in self.catalog.metrics:
                try:
                    m_sel = MetricSelection(metric=m_name)
                    m_built = build_query(m_sel, self.catalog)
                    m_res = self.warehouse.run(m_built.sql, m_built.params)
                    if m_res.rows and m_res.rows[0][0] is not None:
                        val = m_res.rows[0][0]
                        m_obj = self.catalog.metrics[m_name]
                        econ_items.append(f"{m_obj.label}: **{format_value(val, m_obj.format)}**")
                except Exception:
                    pass

        if econ_items:
            lines.append("### Operating Economics")
            lines.append("Aggregate performance: " + " · ".join(econ_items) + ".\n")

        if not suggestions:
            suggestions = [
                "What was total revenue in 2026Q2?",
                "Which products should we restock?",
                "Why did sales decline last quarter?",
            ]
        elif len(suggestions) < 3:
            suggestions.append("Show revenue by category for 2026Q2.")
            suggestions.append("Summarize customer complaints this month.")
        suggestions = suggestions[:3]

        lines.append("**Recommended next questions:**")
        for s in suggestions:
            lines.append(f"- {s}")

        return AnswerEnvelope(
            answer="\n".join(lines).strip(),
            route="conversational",
            confidence="high",
            suggestions=suggestions,
        )

    def _dynamic_greeting_envelope(self, q: str) -> AnswerEnvelope:
        metrics = self.catalog.metric_names()
        dims = self.catalog.dimension_names()
        ordered_metrics = (
            ["revenue"] + [m for m in metrics if m != "revenue"]
            if "revenue" in metrics else metrics
        )
        sample_metrics = ", ".join(f"`{m}`" for m in ordered_metrics[:4])
        sample_dims = ", ".join(f"`{d}`" for d in dims[:4])
        primary_metric = ordered_metrics[0] if ordered_metrics else "revenue"

        answer = (
            "Hello! Welcome to **InsightGPT**, your enterprise analytics workspace.\n\n"
            "I am directly connected to your live data warehouse and governed semantic layer with "
            "zero SQL hallucination:\n"
            f"- **Governed Metrics ({len(metrics)})**: {sample_metrics}...\n"
            f"- **Exploration Dimensions ({len(dims)})**: {sample_dims}...\n"
            "- **Mathematical Attribution**: Root-cause variance decomposition across segments\n"
            "- **Grounded Documents**: Hybrid search across customer tickets and delivery notes\n\n"
            "What would you like to explore today?"
        )
        suggestions = [
            f"What was total {primary_metric} in 2026Q2?",
            (
                "Which products should we restock?"
                if "units_on_hand" in metrics
                else "Show top items."
            ),
            "Why did sales decline last quarter?",
        ]
        return AnswerEnvelope(
            answer=answer,
            route="conversational",
            confidence="high",
            suggestions=suggestions,
        )

    def _dynamic_capabilities_envelope(self) -> AnswerEnvelope:
        additive_metrics = [m.name for m in self.catalog.metrics.values() if m.additive]
        ratio_metrics = [m.name for m in self.catalog.metrics.values() if not m.additive]
        dims = self.catalog.dimension_names()

        answer = (
            "I am **InsightGPT**, an enterprise conversational analytics workspace designed "
            "to make warehouse data directly accessible through plain language.\n\n"
            "**Core Capabilities:**\n"
            "- **Governed Metric Execution**: Compute exact figures using predefined models.\n"
            f"  - Additive: {', '.join(f'`{m}`' for m in additive_metrics)}\n"
            f"  - Ratios & Rates: {', '.join(f'`{m}`' for m in ratio_metrics)}\n"
            f"  - Slice & Dice: {', '.join(f'`{d}`' for d in dims)}\n"
            "- **Root-Cause Attribution**: Decompose changes across dimensions with "
            "mathematical contribution formulas.\n"
            "- **Inventory Prioritization**: Identify depleted stock levels to guide restocking.\n"
            "- **Qualitative Document RAG**: Retrieve cited customer feedback and records.\n"
            "- **Anomaly Detection**: Surface statistical period-over-period variances."
        )
        suggestions = [
            "Show revenue by category for 2026Q2.",
            "Why did sales decline last quarter?",
            "Which products should we restock?",
        ]
        return AnswerEnvelope(
            answer=answer,
            route="conversational",
            confidence="high",
            suggestions=suggestions,
        )

    def _dynamic_suggestions_envelope(self) -> AnswerEnvelope:
        metrics = self.catalog.metric_names()
        primary_metric = (
            "revenue" if "revenue" in metrics else (metrics[0] if metrics else "revenue")
        )
        answer = (
            "Here are high-impact questions you can ask across different business areas:\n\n"
            "**Revenue & Profitability:**\n"
            f"- What was total {primary_metric} in 2026Q2?\n"
            f"- Show {primary_metric} by category for 2026Q2.\n"
            "- What was our gross margin last quarter?\n\n"
            "**Operations & Inventory:**\n"
            "- Which products should we restock?\n"
            "- How many units on hand do we have?\n"
            "- How many orders were placed last quarter?\n\n"
            "**Root-Cause Diagnostics:**\n"
            f"- Why did {primary_metric} decline last quarter?\n"
            "- Show revenue by region last quarter.\n\n"
            "**Customer Experience & Delivery:**\n"
            "- What are customers saying about delivery delays?\n"
            "- Summarize customer complaints this month."
        )
        suggestions = [
            f"Show {primary_metric} by category for 2026Q2.",
            f"Why did {primary_metric} decline last quarter?",
            "Which products should we restock?",
            "What are customers saying about delivery delays?",
        ]
        return AnswerEnvelope(
            answer=answer,
            route="conversational",
            confidence="high",
            suggestions=suggestions,
        )

    def _dynamic_humor_envelope(self, q: str) -> AnswerEnvelope:
        jokes = [
            (
                "Why did the database administrator leave the restaurant?\n"
                "Because they had no *inner join* and all the tables were *full outer*! 😂\n\n"
                "**Bonus:** A SQL query walks into a bar, strolls up to two tables and asks: "
                "*'Can I join you?'* 🍻"
            ),
            (
                "A DBA walks into a doctor's office.\n"
                "**Doctor:** *'What seems to be the problem?'*\n"
                "**DBA:** *'I have NULL pointer exceptions in my heart, and none of my indexes "
                "are being used!'*\n"
                "**Doctor:** *'Have you tried VACUUM ANALYZE?'* 🩺"
            ),
            (
                "Why are machine learning models like bad cooks?\n"
                "Because they both overfit the recipe and fail when served to the public! 🍳"
            ),
            (
                "There are 10 types of people in data engineering:\n"
                "1. Those who understand binary.\n"
                "2. Those who don't.\n"
                "3. Those who didn't expect an off-by-one error in their ETL pipeline! ⚙️"
            ),
            (
                "Why did the data analyst get locked out of their apartment?\n"
                "They lost their *primary key* and their *foreign key* was rejected due to "
                "referential integrity! 🔑"
            ),
            (
                "A statistician tells a colleague: 'Drinking water has a 100% mortality rate!'\n"
                "**Colleague:** *'That's correlation, not causation!'*\n"
                "**Statistician:** *'Tell that to my p-value.'* 📊"
            ),
            (
                "Why do data engineers hate surprises?\n"
                "Because schema drift is never the gift you want to unwrap on a Monday morning! 🎁"
            ),
        ]
        idx = abs(hash(q)) % len(jokes)
        selected_joke = jokes[idx]
        metrics_count = len(self.catalog.metrics)
        dims_count = len(self.catalog.dimensions)

        answer = (
            f"Here is a data engineering & analytics joke for you:\n\n"
            f"{selected_joke}\n\n"
            f"*Ready for real numbers? Your warehouse currently has {metrics_count} governed "
            f"metrics across {dims_count} dimensions ready to query with zero hallucinations.*"
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
