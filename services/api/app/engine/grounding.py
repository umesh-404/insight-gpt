"""Shared question-grounding vocabulary and checks.

This module is the **single home** for the keyword vocabulary the engine and the
providers reason over. It exists because the same lists had been copied into the
offline provider, the hosted provider and the structured path, and they drifted:
abstention behaved correctly offline and not at all with the hosted model.

Two distinct jobs live here, deliberately kept apart:

* **Detection** (:func:`detect_metric`, :func:`detect_group_dims`) — the
  rules-based reading of a question used by the offline provider. These lists
  are narrow and ordered; changing them changes what gets answered.
* **Grounding** (:func:`metric_is_grounded`, :func:`dimension_is_grounded`) — a
  *check*, not a choice: does the question actually contain language that
  supports a selection some provider has already made? The vocabularies here are
  deliberately a superset of the detection lists, because a grounding check that
  is too narrow refuses legitimate questions.

The grounding check is what stops a closed-enum model from answering "what is
the average employee salary by department" with revenue: asked to pick from the
governed menu, a model must pick *something*, so the selection it returns is
only trustworthy when the question supports it.
"""

from __future__ import annotations

import re

# --- detection vocabulary (ordered; first match wins) -------------------------
METRIC_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("restock", "inventory", "stock", "on hand", "on-hand"), "units_on_hand"),
    (("margin",), "gross_margin"),
    (("return",), "return_rate"),
    (("average order", "aov", "basket", "average sale", "avg sale"), "avg_order_value"),
    (("units", "quantity", "sold"), "units_sold"),
    (("orders", "order count"), "orders"),
    (("revenue", "sales", "sale", "turnover", "top-line"), "revenue"),
)

CHANGE_WORDS = ("why", "decline", "declin", "drop", "fell", "fall", "down",
                "decreas", "increas", "grew", "growth", "rose", "up ")

# Words that name a real business metric InsightGPT does NOT govern. When the
# question asks for one of these, the engine abstains (and suggests the closest
# governed metric) rather than quietly answering a different metric.
UNKNOWN_METRIC_HINTS = ("churn", "retention", "attrition", "nps", "csat",
                        "ltv", "lifetime value", "cac", "conversion", "bounce",
                        "engagement", "sentiment score", "profit margin")

# Any explicit time reference. Note that a time word alone is NOT evidence of
# analytics intent: "how many support staff did we hire in 2025" has a year and
# is still unanswerable from the warehouse. See :func:`has_analytics_intent`.
TIME_WORDS = ("quarter", "month", "year", "week", "today", "yesterday",
              "q1", "q2", "q3", "q4", "2024", "2025", "2026", "2027")

DOC_WORDS = ("complain", "complaint", "review", "feedback", "summar", "theme",
             "saying", "sentiment", "issue")

# Phrases that express grouping/ordering intent over a dimension.
EXTREMUM_WORDS = (
    "least sale", "lowest sale", "minimum sale", "lowest selling", "worst sale",
    "worst selling", "least revenue", "lowest revenue", "teh least sale",
    "highest sale", "top sale", "maximum sale", "best sale", "best selling",
    "highest selling", "highest revenue", "top revenue", "most sale",
)

# Explicit "break this down over time" phrasings. Plain period words such as
# "last quarter" are intentionally absent: they scope a question, they do not
# ask for a per-period breakdown.
DATE_GROUPING_WORDS = (
    "by day", "by date", "per day", "by month", "per month", "by quarter",
    "per quarter", "by year", "per year", "each day", "each month", "each year",
    "day by day", "month by month", "over time", "trend", "timeline",
    "time series", "daily", "monthly", "quarterly", "yearly", "sales day",
    "highest day", "best day", "worst day", "vs", "versus",
    "over 2 years", "over two years", "last 2 years", "last two years",
)

_GROUP_DIM_TOKENS: tuple[tuple[str, str], ...] = (
    ("by region", "region"), ("by category", "category"),
    ("by product", "product"), ("by channel", "channel"),
    ("per region", "region"), ("per category", "category"),
    ("per product", "product"), ("per channel", "channel"),
    ("which category", "category"), ("which region", "region"),
    ("what product", "product"), ("what products", "product"),
    ("by date", "date"), ("by day", "date"), ("per day", "date"),
    ("sales day", "date"), ("highest day", "date"), ("highest sales day", "date"),
)

# --- grounding vocabulary (a superset of detection; permissive on purpose) ----
METRIC_VOCABULARY: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales", "sale", "sold", "turnover", "top-line", "topline",
                "income", "gmv", "earning", "made", "money", "rupee", "inr", "amount"),
    "gross_margin": ("margin", "gross profit", "profit", "cogs", "cost of goods"),
    "gross_margin_pct": ("margin", "gross profit", "profit", "cogs", "cost of goods",
                         "percent", "percentage", "%"),
    "orders": ("order", "orders", "transaction", "purchase", "checkout", "basket count"),
    "units_sold": ("unit", "units", "quantity", "qty", "volume", "sold", "items sold",
                   "pieces"),
    "avg_order_value": ("average order", "avg order", "aov", "basket", "average sale",
                        "avg sale", "average ticket", "average value", "average spend"),
    "return_rate": ("return", "returned", "refund", "sent back"),
    "units_on_hand": ("on hand", "on-hand", "inventory", "stock", "restock", "reorder",
                      "replenish", "warehouse level", "in stock", "depleted"),
}

DIMENSION_VOCABULARY: dict[str, tuple[str, ...]] = {
    "region": ("region", "regions", "regional", "geograph", "north", "south",
               "east", "west", "zone", "territory"),
    "category": ("category", "categories", "electronics", "apparel", "home",
                 "beauty", "sports", "product line"),
    "subcategory": ("subcategory", "subcategories", "sub-category"),
    "product": ("product", "products", "sku", "skus", "item", "items", "model"),
    "channel": ("channel", "channels", "online", "in-store", "in store",
                "marketplace", "mobile app", "storefront"),
    "segment": ("segment", "segments", "customer type", "cohort"),
    "store": ("store", "stores", "outlet", "branch", "location"),
    "date": DATE_GROUPING_WORDS,
}

# Phrasings that ask about the business in general without naming a metric.
# These keep an open-ended but legitimate question answerable with the default
# governed metric. A bare time word is deliberately excluded.
GENERIC_INTENT_WORDS = (
    "how did we do", "how are we doing", "how is the business", "how's business",
    "how did the business", "performance", "overall", "business", "kpi", "kpis",
    "metrics", "overview", "summary", "summarise", "summarize", "total",
    "how much did we", "top line", "bottom line", "numbers",
)

_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


# --- detection ----------------------------------------------------------------
def detect_metric(q: str, metrics: list[str] | None = None) -> str | None:
    """First governed metric whose detection keywords appear in ``q``."""
    for words, metric in METRIC_KEYWORDS:
        if any(w in q for w in words) and (not metrics or metric in metrics):
            return metric
    return None


def detect_unknown_metric(q: str) -> str | None:
    """Name a requested-but-ungoverned metric, so the engine can abstain on it."""
    for hint in UNKNOWN_METRIC_HINTS:
        if hint in q:
            # Keep "rate"/"ratio" phrasing when present, so the suggestion reads
            # naturally (e.g. "churn rate" -> suggest "return_rate").
            if "rate" in q and not hint.endswith(("nps", "csat", "score")):
                return f"{hint} rate"
            return hint
    return None


def detect_group_dims(q: str) -> list[str]:
    """Grouping dimensions the question explicitly asks to break down by."""
    dims = [dim for token, dim in _GROUP_DIM_TOKENS if token in q]
    if dims:
        return dims
    if any(w in q for w in DATE_GROUPING_WORDS):
        return ["date"]
    if any(w in q for w in EXTREMUM_WORDS):
        if any(w in q for w in ("day", "date", "daily", "when")):
            return ["date"]
        if any(w in q for w in ("category", "categories")):
            return ["category"]
        if any(w in q for w in ("region", "regions")):
            return ["region"]
        return ["product"]
    return []


# --- grounding ----------------------------------------------------------------
def _name_tokens(name: str) -> tuple[str, ...]:
    return tuple(t for t in name.lower().split("_") if len(t) > 2)


def metric_is_grounded(question: str, metric: str | None) -> bool:
    """True when ``question`` contains language that supports ``metric``.

    A provider that must choose from a closed enum always returns *some* metric,
    so the engine treats the choice as a hypothesis and checks it against the
    question before computing a number from it.
    """
    if not metric:
        return False
    q = question.lower()
    vocab = METRIC_VOCABULARY.get(metric)
    if vocab is None:
        vocab = _name_tokens(metric)
    return any(w in q for w in vocab)


def dimension_is_grounded(question: str, dimension: str | None) -> bool:
    """True when ``question`` actually asks to slice by ``dimension``."""
    if not dimension or dimension == "none":
        return False
    q = question.lower()
    vocab = DIMENSION_VOCABULARY.get(dimension)
    if vocab is None:
        vocab = _name_tokens(dimension)
    return any(w in q for w in vocab)


def has_analytics_intent(question: str) -> bool:
    """True when the question asks something of the warehouse at all.

    Used only as a fallback when no metric vocabulary matched, so an open-ended
    but legitimate question ("how did we do last quarter?") still answers with
    the default governed metric. A bare time reference is **not** enough — a
    year in "how many support staff did we hire in 2025" is what made that
    question look researched while being answered with revenue.
    """
    q = question.lower()
    return (
        any(w in q for w in CHANGE_WORDS)
        or any(w in q for w in DOC_WORDS)
        or any(w in q for w in EXTREMUM_WORDS)
        or any(w in q for w in GENERIC_INTENT_WORDS)
        or any(dimension_is_grounded(q, d) for d in DIMENSION_VOCABULARY)
    )


def selection_is_grounded(question: str, metric: str | None) -> bool:
    """The engine's gate: may we compute ``metric`` for ``question``?"""
    return metric_is_grounded(question, metric) or has_analytics_intent(question)


# --- period grounding ---------------------------------------------------------
def question_years(question: str) -> list[int]:
    """Explicit four-digit years named in the question."""
    return [int(m) for m in _YEAR_RE.findall(question)]


def years_covered(time_range: dict | None, years: list[int]) -> bool:
    """True when ``time_range`` covers at least one of ``years``.

    A resolved period that covers *none* of the years the question named means
    the resolver fell back to its default (``last quarter``) and the answer would
    silently be about a different period than the one asked about.
    """
    if not years or not time_range:
        return True
    try:
        start = int(str(time_range["start"])[:4])
        end = int(str(time_range["end"])[:4])
    except (KeyError, TypeError, ValueError):
        return True
    return any(start <= y <= end for y in years)


def year_span(years: list[int]) -> dict:
    """A full-calendar range spanning ``years`` (used to honour the asked period)."""
    return {"start": f"{min(years)}-01-01", "end": f"{max(years)}-12-31"}
