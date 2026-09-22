"""TypeSafe AI (Jev System One) provider.

Leverages TypeSafe's System One decision model (Jev) to make fast, typed
judgments (Choice and Noul primitives) for routing, metric selection, dimension
breakdowns, and change detection, while code owns deterministic warehouse execution
and synthesis.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .base import Provider
from .fake import (
    FakeProvider,
    _detect_entities,
    _detect_unknown_metric,
    _parse_date,
    _payload_of,
    _resolve_time,
    _task_of,
)

logger = logging.getLogger(__name__)


class TypeSafeProvider(Provider):
    name = "typesafe"

    def __init__(self, api_key: str | None = None, model: str = "jev-latest", timeout: float = 30.0):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self.model = model or os.getenv("TYPESAFE_MODEL", "jev-latest")
        self.timeout = timeout
        self.fallback = FakeProvider()
        self._client = None

        if self.api_key:
            try:
                from typesafe_sdk import TypeSafeClient
                self._client = TypeSafeClient(api_key=self.api_key, timeout=self.timeout)
            except Exception as e:
                logger.warning("Failed to initialize TypeSafeClient (%s); falling back to offline", e)

    def complete(self, prompt: str, **opts) -> str:
        task = _task_of(prompt)
        payload = _payload_of(prompt)

        if not self._client:
            return self.fallback.complete(prompt, **opts)

        try:
            if task == "route":
                return json.dumps(self._route_with_jev(payload))
            if task == "synthesize":
                # Jev is a System One decision model; synthesis of validated findings
                # is driven by code templates and grounded evidence.
                return self.fallback.complete(prompt, **opts)
            return self.fallback.complete(prompt, **opts)
        except Exception as err:
            logger.warning("TypeSafe Jev evaluation error (%s); using fallback", err)
            return self.fallback.complete(prompt, **opts)

    def _route_with_jev(self, p: dict) -> dict:
        from typesafe_sdk import Choice, Noul

        q = str(p.get("question", "")).strip()
        today_str = p.get("today", "2026-07-15")
        metrics = list(p.get("metrics", []))
        dimensions = list(p.get("dimensions", []))

        metric_criteria: dict[str, Any] = {m: None for m in metrics}
        metric_criteria["none"] = "None of the listed governed metrics"

        dim_criteria: dict[str, Any] = {d: None for d in dimensions}
        dim_criteria["none"] = "No grouping or slice dimension"

        questions = {
            "route": Choice(
                instructions=(
                    "Classify this user question for a business analytics and operations engine. "
                    "Select: "
                    "'structured' if answerable from warehouse tables and metrics alone; "
                    "'unstructured' if asking about text documents, reviews, or customer feedback alone; "
                    "'hybrid' if asking for both metrics and reasons/causes/thematic documents (e.g. 'why did revenue drop'); "
                    "'conversational' if asking about capabilities, help, greetings, or who the assistant is."
                ),
                criteria={
                    "structured": "Answerable from metrics/data alone",
                    "unstructured": "Answerable from customer feedback or documents alone",
                    "hybrid": "Requires numbers plus causal or thematic documents",
                    "conversational": "General capabilities, help, or greetings",
                },
            ),
            "is_change": Noul(
                instructions="Is this question asking why a metric changed, grew, declined, or comparing periods?",
            ),
            "metric": Choice(
                instructions="Which governed metric is requested by the user?",
                criteria=metric_criteria,
            ),
            "dimension": Choice(
                instructions="Which dimension is requested to group or slice by?",
                criteria=dim_criteria,
            ),
        }

        res = self._client.system_one(
            state={"question": q, "today": today_str},
            questions=questions,
            model=self.model,
        )

        route_choice = res.choices["route"].choice
        is_change = res.nouls["is_change"].noul >= 0.5
        metric_choice = res.choices["metric"].choice
        dim_choice = res.choices["dimension"].choice

        metric = None if metric_choice == "none" else metric_choice
        group_dims = [] if dim_choice == "none" else [dim_choice]

        if route_choice == "conversational":
            return {
                "route": "conversational",
                "metric": None,
                "time_range": None,
                "prior_time_range": None,
                "group_dims": [],
                "entities": {},
                "is_change_question": False,
                "needs_docs": False,
                "clarify": None,
            }

        # Resolve dates and entities
        today = _parse_date(today_str)
        q_lower = q.lower()
        time_range, prior = _resolve_time(q_lower, today, need_prior=is_change)
        entities = _detect_entities(q_lower)

        # Detect any requested-but-ungoverned metric name so engine can abstain cleanly
        unknown_metric = _detect_unknown_metric(q_lower) if metric is None else None

        return {
            "route": route_choice,
            "metric": metric or unknown_metric,
            "time_range": time_range,
            "prior_time_range": prior,
            "group_dims": group_dims,
            "entities": entities,
            "is_change_question": is_change,
            "needs_docs": route_choice in ("unstructured", "hybrid"),
            "clarify": None,
        }
