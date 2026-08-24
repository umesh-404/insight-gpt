"""The semantic layer is defined twice — this test stops the two from drifting.

Two files describe the same eight governed metrics:

* ``config/semantic_layer.yml`` — what the API's deterministic query builder
  actually reads and compiles into SQL;
* ``services/warehouse/models/metrics/metrics.yml`` — the dbt/MetricFlow
  definitions, so the same metrics are versioned inside the warehouse project
  and validated by ``dbt parse``.

Neither can simply be deleted: the engine cannot call MetricFlow, and dbt cannot
read the engine's catalog. The real risk is not the duplication, it is
duplication that *silently diverges* — someone adds a metric to one file, the
other keeps answering with the old definition, and nothing complains. So the
duplication stays and this test makes divergence loud:

* the metric NAMES must be identical sets;
* each metric's LABEL must match;
* each metric's aggregation must agree — the engine's SQL ``expr`` and the dbt
  measure/ratio it maps to describe the same computation;
* every dimension the engine allows a metric to be sliced by must exist as a
  dbt dimension on some semantic model.

Runs offline; it only parses YAML.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_CATALOG = REPO_ROOT / "config" / "semantic_layer.yml"
DBT_METRICS = REPO_ROOT / "services" / "warehouse" / "models" / "metrics" / "metrics.yml"

# dbt metrics that exist only as building blocks for a ratio metric; they are
# deliberately absent from the engine catalog, which exposes the ratio itself.
BUILDING_BLOCKS = {"returned_units", "total_units"}


@pytest.fixture(scope="module")
def engine() -> dict:
    return yaml.safe_load(ENGINE_CATALOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dbt() -> dict:
    return yaml.safe_load(DBT_METRICS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dbt_metrics(dbt) -> dict[str, dict]:
    return {m["name"]: m for m in dbt["metrics"]}


@pytest.fixture(scope="module")
def dbt_measures(dbt) -> dict[str, dict]:
    measures: dict[str, dict] = {}
    for model in dbt["semantic_models"]:
        for measure in model.get("measures", []) or []:
            measures[measure["name"]] = measure
    return measures


@pytest.fixture(scope="module")
def dbt_dimensions(dbt) -> set[str]:
    names: set[str] = set()
    for model in dbt["semantic_models"]:
        for dim in model.get("dimensions", []) or []:
            names.add(dim["name"])
            if expr := dim.get("expr"):
                names.add(expr)
    return names


def _normalize_sql(expr: str) -> str:
    """Collapse SQL to a comparable form: lowercase, no spaces, no parens."""
    return re.sub(r"[\s()]+", "", expr).lower()


# --------------------------------------------------------------------------- #
# metric sets                                                                  #
# --------------------------------------------------------------------------- #
def test_metric_names_match(engine, dbt_metrics):
    engine_names = set(engine["metrics"])
    dbt_names = set(dbt_metrics) - BUILDING_BLOCKS
    assert engine_names == dbt_names, (
        "semantic layer drift: "
        f"only in config/semantic_layer.yml: {sorted(engine_names - dbt_names)}; "
        f"only in metrics.yml: {sorted(dbt_names - engine_names)}"
    )


def test_building_blocks_are_not_exposed_to_the_engine(engine):
    # If one of these ever needs to be answerable, add it to the engine catalog
    # deliberately — do not let it leak in.
    assert BUILDING_BLOCKS.isdisjoint(engine["metrics"])


def test_labels_match(engine, dbt_metrics):
    mismatched = {
        name: (spec["label"], dbt_metrics[name]["label"])
        for name, spec in engine["metrics"].items()
        if name in dbt_metrics and spec["label"] != dbt_metrics[name]["label"]
    }
    assert not mismatched, f"label drift (engine, dbt): {mismatched}"


# --------------------------------------------------------------------------- #
# definitions                                                                  #
# --------------------------------------------------------------------------- #
# Engine metric -> how the same computation is expressed in dbt. A `simple`
# metric maps to one measure; a `ratio` maps to a numerator/denominator pair.
EXPECTED_DBT_SHAPE: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": ("simple", ("revenue_amount",)),
    "gross_margin": ("simple", ("margin_amount",)),
    "gross_margin_pct": ("ratio", ("gross_margin", "revenue")),
    "orders": ("simple", ("distinct_orders",)),
    "units_sold": ("simple", ("units_non_returned",)),
    "avg_order_value": ("ratio", ("revenue", "orders")),
    "return_rate": ("ratio", ("returned_units", "total_units")),
    "units_on_hand": ("simple", ("on_hand",)),
}


def test_every_metric_has_a_declared_mapping(engine):
    assert set(engine["metrics"]) == set(EXPECTED_DBT_SHAPE), (
        "a metric was added or removed without updating EXPECTED_DBT_SHAPE — "
        "the mapping between the two definitions must stay explicit"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_DBT_SHAPE))
def test_metric_definition_agrees(name, engine, dbt_metrics, dbt_measures):
    kind, parts = EXPECTED_DBT_SHAPE[name]
    metric = dbt_metrics[name]
    assert metric["type"] == kind, f"{name}: dbt type is {metric['type']}, expected {kind}"

    engine_expr = _normalize_sql(engine["metrics"][name]["expr"])

    if kind == "simple":
        measure_name = metric["type_params"]["measure"]
        assert measure_name == parts[0], (
            f"{name}: dbt measure is {measure_name!r}, expected {parts[0]!r}"
        )
        measure = dbt_measures[measure_name]
        # SUM(gross_revenue - discount_amount) <-> agg: sum over that expr.
        agg, expr = measure["agg"], _normalize_sql(str(measure["expr"]))
        expected = f"{'countdistinct' if agg == 'count_distinct' else agg}{expr}"
        assert engine_expr == expected, (
            f"{name}: engine expr {engine['metrics'][name]['expr']!r} does not match "
            f"dbt measure {agg}({measure['expr']})"
        )
    else:
        numerator = metric["type_params"]["numerator"]
        denominator = metric["type_params"]["denominator"]
        assert (numerator, denominator) == parts, (
            f"{name}: dbt ratio is {numerator}/{denominator}, expected {parts[0]}/{parts[1]}"
        )
        # The engine writes the ratio inline; both halves must appear in it.
        for component in parts:
            source = (
                engine["metrics"].get(component, {}).get("expr")
                or _measure_expr(dbt_metrics, dbt_measures, component)
            )
            assert _normalize_sql(source) in engine_expr, (
                f"{name}: engine expr does not contain the {component!r} computation"
            )


def _measure_expr(dbt_metrics: dict, dbt_measures: dict, metric_name: str) -> str:
    """The SQL-ish text of a building-block metric, for containment checks."""
    measure = dbt_measures[dbt_metrics[metric_name]["type_params"]["measure"]]
    return str(measure["expr"])


# --------------------------------------------------------------------------- #
# dimensions                                                                   #
# --------------------------------------------------------------------------- #
def test_every_engine_dimension_exists_in_dbt(engine, dbt_dimensions):
    """Each engine dimension's SQL column must be a dbt dimension too.

    ``date`` is excluded: it is the metrics' time dimension, declared per
    semantic model as ``order_date`` / ``snapshot_date`` rather than as a
    conformed attribute.
    """
    missing = {
        name: spec["expr"]
        for name, spec in engine["dimensions"].items()
        if name != "date" and spec.get("expr") and spec["expr"] not in dbt_dimensions
    }
    assert not missing, f"dimensions in the engine catalog with no dbt counterpart: {missing}"


def test_metric_dimension_allow_lists_reference_known_dimensions(engine):
    known = set(engine["dimensions"])
    unknown = {
        name: sorted(set(spec.get("dimensions", [])) - known)
        for name, spec in engine["metrics"].items()
        if set(spec.get("dimensions", [])) - known
    }
    assert not unknown, f"metrics sliced by undefined dimensions: {unknown}"


def test_allow_list_covers_every_fact_and_dimension_table(engine):
    """The guardrail allow-list must contain every table the metrics can reach."""
    allowed = set(engine["allow_list"]["tables"])
    needed = set(engine["facts"])
    for fact in engine["facts"].values():
        needed |= set(fact.get("joins", {}))
    for dim in engine["dimensions"].values():
        needed.add(dim["table"])
    assert needed <= allowed, f"tables reachable but not allow-listed: {sorted(needed - allowed)}"
