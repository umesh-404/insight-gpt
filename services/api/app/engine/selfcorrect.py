"""Bounded self-correction for the governed structured path.

The reliability keystone of this engine is that the model selects *governed
metrics/dimensions* and deterministic code emits the SQL. Self-correction stays
strictly inside that boundary: when a selection fails (a guardrail/catalog
rejection, a warehouse error, or a clearly-wrong empty result), the loop asks
the provider for a **corrected governed selection** — never free SQL — validates
it against the catalog exactly as the first selection was validated, and retries.

The loop is *bounded* (``max_retries``, default 2) and *loop-safe*: a selection
signature that has already been tried is never re-run, so a provider that keeps
returning the same bad selection makes the loop give up rather than spin. When
correction is exhausted the caller abstains (:class:`AbstainSignal`) instead of
fabricating an answer. See ``docs/05-insight-engine.md`` §9.
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel

from ..providers.base import Provider, extract_json
from ..semantic.catalog import CatalogError, SemanticCatalog
from ..semantic.query_builder import BuiltQuery, MetricSelection, build_query
from ..warehouse.executor import QueryResult, Warehouse
from .envelope import CorrectionAttempt
from .prompts import correction_prompt


class AbstainSignal(Exception):
    """Raised when a governed query cannot be made valid within the retry budget.

    The engine turns this into an honest ``route="abstain"`` envelope — a refusal
    with a reason and concrete suggestions, never a fabricated number.
    """

    def __init__(self, reason: str, suggestions: list[str],
                 attempts: list[CorrectionAttempt] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.suggestions = suggestions
        self.attempts = attempts or []


class ExecOutcome(BaseModel):
    """Result of running a selection through the corrector.

    ``status`` is one of:
      * ``"ok"``     — executed and returned rows.
      * ``"empty"``  — executed cleanly but the warehouse genuinely had no rows
                       (a *well-formed* query with no data — NOT a failure and
                       NOT a reason to abstain).
      * ``"failed"`` — could not be made valid within the retry budget.
    """

    status: str
    selection: MetricSelection | None = None
    built: BuiltQuery | None = None
    result: QueryResult | None = None
    attempts: list[CorrectionAttempt] = []
    error: str | None = None


def suggest_metrics(name: object, catalog: SemanticCatalog) -> list[str]:
    """Suggest the closest governed metric(s) to an unknown/failed metric name."""
    names = catalog.metric_names()
    close = difflib.get_close_matches(str(name or ""), names, n=3, cutoff=0.3)
    picks = close or names[:3]
    return [f"Try the governed metric '{p}'." for p in picks]


def _selection_view(sel: MetricSelection) -> dict:
    """A compact, JSON-friendly view of a selection for the attempt log."""
    return {
        "metric": sel.metric,
        "dimensions": list(sel.dimensions),
        "time_grain": sel.time_grain,
        "order_by_metric": sel.order_by_metric,
    }


def _sig(sel: MetricSelection) -> tuple:
    """A hashable signature so an already-tried selection is never re-run."""
    return (
        sel.metric,
        tuple(sel.dimensions),
        sel.time_grain,
        sel.order_by_metric,
        tuple((f.dimension, f.op, tuple(str(v) for v in f.values)) for f in sel.filters),
    )


def _is_empty(result: QueryResult) -> bool:
    """True when the result carries no data.

    A single all-``NULL`` row (an aggregate over zero matching rows) is treated
    as empty, so a genuine "no data" is never mistaken for a real ``0``.
    """
    if not result.rows:
        return True
    return len(result.rows) == 1 and all(cell is None for cell in result.rows[0])


class Corrector:
    """Executes governed selections with a bounded, catalog-validated retry loop."""

    def __init__(self, catalog: SemanticCatalog, warehouse: Warehouse,
                 provider: Provider, question: str = "", *, max_retries: int = 2):
        self.catalog = catalog
        self.warehouse = warehouse
        self.provider = provider
        self.question = question
        self.max_retries = max_retries

    def execute(self, selection: MetricSelection, *, stage: str,
                expect_rows: bool = True) -> ExecOutcome:
        attempts: list[CorrectionAttempt] = []
        current = selection
        seen: set[tuple] = {_sig(current)}
        last_error: str | None = None

        for i in range(self.max_retries + 1):
            try:
                built = build_query(current, self.catalog)
                result = self.warehouse.run(built.sql, built.params)
            except Exception as exc:  # noqa: BLE001 — any failure is a correction trigger
                last_error = f"{type(exc).__name__}: {exc}"
                corrected = self._correct(current, last_error, stage)
                usable = corrected is not None and _sig(corrected) not in seen
                attempts.append(CorrectionAttempt(
                    attempt=i + 1, stage=stage, selection=_selection_view(current),
                    error=last_error, resolution="corrected" if usable else "gave_up",
                ))
                if not usable:
                    return ExecOutcome(status="failed", attempts=attempts, error=last_error)
                seen.add(_sig(corrected))
                current = corrected
                continue

            # Executed cleanly. An empty result where rows were expected gets one
            # correction attempt; if nothing better is found it is honest "no
            # data", not a failure.
            if expect_rows and _is_empty(result):
                corrected = self._correct(current, "query returned zero rows", stage)
                if corrected is not None and _sig(corrected) not in seen and i < self.max_retries:
                    attempts.append(CorrectionAttempt(
                        attempt=i + 1, stage=stage, selection=_selection_view(current),
                        error="empty result where rows expected", resolution="corrected",
                    ))
                    seen.add(_sig(corrected))
                    current = corrected
                    continue
                return ExecOutcome(status="empty", selection=current, built=built,
                                   result=result, attempts=attempts)

            return ExecOutcome(status="ok", selection=current, built=built,
                               result=result, attempts=attempts)

        return ExecOutcome(status="failed", attempts=attempts, error=last_error)

    # ---- correction strategies ----------------------------------------------
    def _correct(self, current: MetricSelection, error: str,
                 stage: str) -> MetricSelection | None:
        """Return a corrected governed selection, or ``None`` to give up.

        Tries the provider first (the intended LLM path), then a conservative
        deterministic repair so the loop still recovers offline with a
        rules-based provider.
        """
        by_provider = self._provider_correction(current, error, stage)
        if by_provider is not None:
            return by_provider
        return self._deterministic_repair(current)

    def _provider_correction(self, current: MetricSelection, error: str,
                             stage: str) -> MetricSelection | None:
        try:
            prompt = correction_prompt(
                self.question, _selection_view(current), error,
                self.catalog.metric_names(), self.catalog.dimension_names(),
                self._metric_dims(current.metric),
            )
            raw = self.provider.complete(prompt, json=True, temperature=0.0)
            obj = extract_json(raw)
        except Exception:  # noqa: BLE001 — a provider that can't correct is a give-up, not a crash
            return None
        if not isinstance(obj, dict) or obj.get("error") or "metric" not in obj:
            return None
        return self._build_governed(obj, current)

    def _deterministic_repair(self, current: MetricSelection) -> MetricSelection | None:
        """Fix the two mechanical faults that don't need a model: an off-allow-list
        grouping dimension, or a stray ordering. Never guesses a metric."""
        try:
            metric = self.catalog.resolve_metric(current.metric)
        except CatalogError:
            return None  # can't invent a metric — the caller abstains
        bad = [d for d in current.dimensions
               if d not in self.catalog.dimensions or d not in metric.dimensions]
        if bad:
            keep = [d for d in current.dimensions if d not in bad]
            return current.model_copy(update={"dimensions": keep})
        if current.order_by_metric:
            return current.model_copy(update={"order_by_metric": None})
        return None

    def _build_governed(self, obj: dict, current: MetricSelection) -> MetricSelection | None:
        """Construct a selection from a provider correction, validated against the
        catalog exactly as the original selection was — never a free-SQL hatch."""
        base = current.model_dump()
        for key in ("metric", "dimensions", "time_grain", "order_by_metric", "limit"):
            if key in obj and obj[key] is not None:
                base[key] = obj[key]
        if isinstance(obj.get("filters"), list):
            base["filters"] = obj["filters"]
        try:
            sel = MetricSelection(**base)
        except Exception:  # noqa: BLE001 — malformed correction is a give-up
            return None
        try:
            metric = self.catalog.resolve_metric(sel.metric)
        except CatalogError:
            return None
        for d in sel.dimensions:
            if d not in self.catalog.dimensions or d not in metric.dimensions:
                return None
        return sel

    def _metric_dims(self, name: str) -> list[str]:
        try:
            return self.catalog.resolve_metric(name).dimensions
        except CatalogError:
            return []
