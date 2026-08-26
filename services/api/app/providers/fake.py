"""Deterministic, offline provider.

Not an LLM — a rules-based stand-in that reads the ``TASK`` + ``PAYLOAD`` in a
prompt and returns valid JSON. It exists so the engine and its tests run with no
external services, and doubles as a stable baseline for the eval harness. Real
reasoning quality comes from the Ollama/cloud providers; this only needs to be
*correct and predictable*, not clever.
"""

from __future__ import annotations

import calendar
import json
from datetime import date

from ..formatting import format_value
from .base import Provider

_METRIC_KEYWORDS = [
    (("restock", "inventory", "stock", "on hand", "on-hand"), "units_on_hand"),
    (("margin",), "gross_margin"),
    (("return",), "return_rate"),
    (("average order", "aov", "basket"), "avg_order_value"),
    (("units", "quantity", "sold"), "units_sold"),
    (("orders", "order count"), "orders"),
    (("revenue", "sales", "turnover"), "revenue"),
]
_CHANGE_WORDS = ("why", "decline", "declin", "drop", "fell", "fall", "down",
                 "decreas", "increas", "grew", "growth", "rose", "up ")
# Words that name a real business metric InsightGPT does NOT govern. When the
# question asks for one of these, the engine should abstain (and suggest the
# closest governed metric) rather than quietly answer a different metric. A real
# LLM router surfaces the requested metric name; this offline stand-in needs the
# hint list to do the same.
_UNKNOWN_METRIC_HINTS = ("churn", "retention", "attrition", "nps", "csat",
                         "ltv", "lifetime value", "cac", "conversion", "bounce",
                         "engagement", "sentiment score", "profit margin")

# Any explicit time reference. Its ABSENCE (together with no metric and no
# document intent) means the question is not an analytics question at all, so the
# provider must not fabricate a period — the engine then abstains.
_TIME_WORDS = ("quarter", "month", "year", "week", "today", "yesterday",
               "q1", "q2", "q3", "q4", "2024", "2025", "2026", "2027")

_DOC_WORDS = ("complain", "complaint", "review", "feedback", "summar", "theme",
              "saying", "sentiment", "issue")


class FakeProvider(Provider):
    name = "fake"

    def complete(self, prompt: str, **opts) -> str:
        task = _task_of(prompt)
        payload = _payload_of(prompt)
        if task == "route":
            return json.dumps(self._route(payload))
        if task == "synthesize":
            return json.dumps(self._synthesize(payload))
        return json.dumps({"error": f"unknown task {task!r}"})

    # ---- routing -------------------------------------------------------------
    def _route(self, p: dict) -> dict:
        q = str(p.get("question", "")).lower()
        today = _parse_date(p.get("today", "2026-07-15"))
        metrics = p.get("metrics", [])

        metric = _detect_metric(q, metrics)
        # A metric that is named but not governed -> reported so the engine can
        # abstain with a suggestion instead of answering the wrong metric.
        unknown_metric = _detect_unknown_metric(q) if metric is None else None
        wants_docs = any(w in q for w in _DOC_WORDS)
        is_change = any(w in q for w in _CHANGE_WORDS) and metric is not None

        if wants_docs and not is_change and metric in (None, "revenue") and "why" not in q:
            route = "unstructured"
            metric = None
        elif is_change or (wants_docs and metric):
            route = "hybrid"
        else:
            route = "structured"

        # Only resolve a period when the question actually has analytics intent.
        # A question with no governed metric, no unknown-metric name, no document
        # ask, no change verb and no time word is not answerable from the
        # warehouse at all — leave the range empty so the engine abstains rather
        # than inventing "last quarter" and answering a default metric.
        has_intent = (
            metric is not None or unknown_metric is not None or is_change
            or wants_docs or any(w in q for w in _TIME_WORDS)
        )
        if has_intent:
            time_range, prior = _resolve_time(q, today, need_prior=is_change)
        else:
            time_range, prior = None, None
        group_dims = _detect_group_dims(q)

        return {
            "route": route,
            # Surface the ungoverned metric name so the router marks it unresolved.
            "metric": metric or unknown_metric,
            "time_range": time_range,
            "prior_time_range": prior,
            "group_dims": group_dims,
            "entities": _detect_entities(q),
            "is_change_question": is_change,
            "needs_docs": route in ("unstructured", "hybrid"),
            "clarify": None,
        }

    # ---- synthesis -----------------------------------------------------------
    def _synthesize(self, p: dict) -> dict:
        f = p.get("findings", {})
        evidence = p.get("evidence", [])
        kind = f.get("kind")
        cites = "".join(f"[{e['n']}]" for e in evidence)

        if kind == "change":
            fmt = f.get("format", "number")
            cur, prior = f["current"], f["prior"]
            pct = f["change_pct"]
            direction = "fell" if pct < 0 else "rose"
            parts = [
                f"{_label(f['metric'])} {direction} {abs(pct):.1f}% "
                f"({_num(prior['value'], fmt)} → {_num(cur['value'], fmt)}) from "
                f"{prior['label']} to {cur['label']}."
            ]
            drivers = []
            if f.get("top_region"):
                tr = f["top_region"]
                drivers.append(f"the {tr['region']} region ({_signed(tr['delta'], fmt)})")
            if f.get("top_category"):
                tc = f["top_category"]
                drivers.append(f"the {tc['category']} category ({_signed(tc['delta'], fmt)})")
            if drivers:
                parts.append("The change was driven mainly by " + " and ".join(drivers) + ".")
            if evidence:
                parts.append(
                    "Documents for the period attribute this to fulfilment delays "
                    f"at the North distribution centre {cites}."
                )
            conf = "high" if evidence else "medium"
            return {"answer": " ".join(parts), "confidence": conf, "caveats": f.get("caveats", [])}

        if kind == "scalar":
            return {
                "answer": f"{_label(f['metric'])} for {f['period']} was "
                          f"{_num(f['value'], f.get('format', 'number'))}.",
                "confidence": "high", "caveats": f.get("caveats", []),
            }

        if kind == "grouped":
            top = f.get("rows", [])[:3]
            gfmt = f.get("format", "number")
            listing = "; ".join(f"{r['label']}: {_num(r['value'], gfmt)}" for r in top)
            return {
                "answer": f"{_label(f['metric'])} by {f['dimension']} for {f['period']} — "
                          f"top: {listing}.",
                "confidence": "high", "caveats": f.get("caveats", []),
            }

        # docs-only
        titles = "; ".join(e.get("title", e["doc_id"]) for e in evidence[:3])
        answer = (
            f"The main themes in the retrieved documents concern fulfilment delays "
            f"and late deliveries, especially in the North region {cites}. "
            f"Representative items: {titles}."
        ) if evidence else "No relevant documents were found for that question."
        return {"answer": answer, "confidence": "medium" if evidence else "low", "caveats": []}


# --- helpers ------------------------------------------------------------------
def _task_of(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("TASK:"):
            return line.split(":", 1)[1].strip()
    return ""


def _payload_of(prompt: str) -> dict:
    idx = prompt.rfind("PAYLOAD:")
    if idx == -1:
        return {}
    return json.loads(prompt[idx + len("PAYLOAD:"):].strip())


def _detect_metric(q: str, metrics: list[str]) -> str | None:
    for words, metric in _METRIC_KEYWORDS:
        if any(w in q for w in words) and (not metrics or metric in metrics):
            return metric
    return None


def _detect_unknown_metric(q: str) -> str | None:
    """Name a requested-but-ungoverned metric, so the engine can abstain on it."""
    for hint in _UNKNOWN_METRIC_HINTS:
        if hint in q:
            # Keep "rate"/"ratio" phrasing when present, so the suggestion reads
            # naturally (e.g. "churn rate" -> suggest "return_rate").
            if "rate" in q and not hint.endswith(("nps", "csat", "score")):
                return f"{hint} rate"
            return hint
    return None


def _detect_group_dims(q: str) -> list[str]:
    dims = []
    for token, dim in (("by region", "region"), ("by category", "category"),
                       ("by product", "product"), ("by channel", "channel"),
                       ("per region", "region"), ("per category", "category")):
        if token in q:
            dims.append(dim)
    return dims


def _detect_entities(q: str) -> dict:
    ents: dict[str, list[str]] = {}
    for region in ("north", "south", "east", "west"):
        if region in q:
            ents.setdefault("region", []).append(region.capitalize())
    for cat in ("electronics", "apparel", "home"):
        if cat in q:
            ents.setdefault("category", []).append(cat.capitalize())
    return ents


def _parse_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _quarter_range(year: int, q: int) -> dict:
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    last_day = calendar.monthrange(year, end_month)[1]
    return {"start": f"{year}-{start_month:02d}-01",
            "end": f"{year}-{end_month:02d}-{last_day:02d}"}


def _prev_quarter(year: int, q: int) -> tuple[int, int]:
    return (year - 1, 4) if q == 1 else (year, q - 1)


def _resolve_time(q: str, today: date, need_prior: bool) -> tuple[dict, dict | None]:
    cy, cq = today.year, _quarter(today)
    if "last quarter" in q or "previous quarter" in q or ("quarter" in q and "this" not in q):
        ly, lq = _prev_quarter(cy, cq)
        cur = _quarter_range(ly, lq)
        py, pq = _prev_quarter(ly, lq)
        prior = _quarter_range(py, pq) if need_prior else None
        return cur, prior
    if "this quarter" in q:
        cur = _quarter_range(cy, cq)
        py, pq = _prev_quarter(cy, cq)
        return cur, (_quarter_range(py, pq) if need_prior else None)
    if "this month" in q:
        last = calendar.monthrange(today.year, today.month)[1]
        return {"start": f"{today.year}-{today.month:02d}-01",
                "end": f"{today.year}-{today.month:02d}-{last:02d}"}, None
    # default: last quarter (keeps demo answers grounded in a real period)
    ly, lq = _prev_quarter(cy, cq)
    cur = _quarter_range(ly, lq)
    py, pq = _prev_quarter(ly, lq)
    return cur, (_quarter_range(py, pq) if need_prior else None)


def _label(metric: str | None) -> str:
    return {"revenue": "Revenue", "gross_margin": "Gross margin", "orders": "Orders",
            "units_sold": "Units sold", "avg_order_value": "Average order value",
            "return_rate": "Return rate", "units_on_hand": "Units on hand",
            }.get(metric or "", (metric or "Value").replace("_", " ").capitalize())


def _num(v, fmt: str = "number") -> str:
    """Render a figure the way the UI will, so prose and tiles agree."""
    return format_value(float(v), fmt)


def _signed(v, fmt: str = "number") -> str:
    """A delta always carries its sign, so a fall reads as one."""
    body = _num(abs(v), fmt)
    return f"+{body}" if v >= 0 else f"-{body}"
