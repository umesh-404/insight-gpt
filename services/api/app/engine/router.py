"""The NL router — the first reasoning step.

Classifies a question (structured / unstructured / hybrid) and extracts the
typed parameters the downstream paths need: metric intent, an explicit time
range, entities, and whether it is a change question. It decides *who answers*,
not the answer. Ambiguity is a first-class outcome — an under-specified question
returns a clarifying question instead of a guess. See ``docs/05-insight-engine.md``
§2.
"""

from __future__ import annotations

from ..providers.base import Provider, extract_json
from ..semantic.catalog import SemanticCatalog
from .prompts import route_prompt

_VALID_ROUTES = {"structured", "unstructured", "hybrid"}


def route(
    question: str,
    catalog: SemanticCatalog,
    provider: Provider,
    today: str,
    attachments: list[dict] | None = None,
) -> dict:
    prompt = route_prompt(question, today, catalog.metric_names(), catalog.dimension_names())
    raw = provider.complete(prompt, json=True, temperature=0.0, images=_images_for(attachments))
    obj = extract_json(raw)
    return _normalize(obj, catalog)


def _images_for(attachments: list[dict] | None) -> list[str]:
    if not attachments:
        return []
    return [item["data"] for item in attachments if item.get("kind") == "image" and item.get("data")]


def _normalize(obj: dict, catalog: SemanticCatalog) -> dict:
    if obj.get("clarify"):
        return {"route": "clarify", "clarify": str(obj["clarify"]), "metric": None,
                "requested_metric": None, "metric_unresolved": False,
                "time_range": None, "prior_time_range": None, "group_dims": [],
                "entities": {}, "is_change_question": False, "needs_docs": False}

    r = obj.get("route")
    if r not in _VALID_ROUTES:
        r = "structured"

    # Resolve/validate the metric against the governed catalog. We keep the raw
    # request and whether it resolved: a metric that was *named but not in the
    # catalog* is an abstention trigger downstream, distinct from an *absent*
    # metric (which safely defaults). This is what stops the engine from quietly
    # answering the wrong metric for an unknown one.
    requested_metric = obj.get("metric")
    metric: str | None = None
    if requested_metric is not None:
        try:
            metric = catalog.resolve_metric(requested_metric).name
        except Exception:
            metric = None
    metric_unresolved = requested_metric is not None and metric is None
    # Default only for a genuinely absent metric, never to paper over a wrong one.
    if r in ("structured", "hybrid") and metric is None and not metric_unresolved:
        metric = "revenue"  # safe governed default

    # Validate group dims against the catalog.
    group_dims = [d for d in obj.get("group_dims", []) if d in catalog.dimensions]

    tr = obj.get("time_range")
    if not (isinstance(tr, dict) and tr.get("start") and tr.get("end")):
        tr = None

    return {
        "route": r,
        "metric": metric,
        "requested_metric": requested_metric,
        "metric_unresolved": metric_unresolved,
        "time_range": tr,
        "prior_time_range": obj.get("prior_time_range"),
        "group_dims": group_dims,
        "entities": obj.get("entities", {}) or {},
        "is_change_question": bool(obj.get("is_change_question")),
        "needs_docs": bool(obj.get("needs_docs")) or r in ("unstructured", "hybrid"),
        "clarify": None,
    }
