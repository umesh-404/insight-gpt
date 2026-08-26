"""Text-to-SQL execution-accuracy harness for the InsightGPT insight engine.

Scores the grounded text-to-SQL path against a golden set answerable on the
deterministic DuckDB fixture warehouse (``InsightEngine.fixture()`` + the
``fake`` provider). Three primary metrics plus an abstention probe:

* **execution accuracy** — did the numbers the engine returned match the
  expected result (within tolerance)? This is measured against the *tables the
  engine executed*, never the prose, because the numbers must come from SQL.
* **routing accuracy** — did the router send the question to the right path
  (structured / unstructured / hybrid)?
* **metric-selection accuracy** — did it pick the governed metric we expected?
* **abstention** (probe) — for questions with an unknown metric or an impossible
  premise, does the engine decline rather than fabricate an answer? The engine's
  abstention envelope is still being built, so this is detected *defensively*
  (any of several signals) and reported, not gated — it grows meaningful the day
  abstention lands, without changing this harness.

Run as a script (prints a scoreboard) or as pytest (``test_text2sql_eval``
asserts the score floors so CI catches regressions)::

    python tests/eval/text2sql.py
    pytest tests/eval/text2sql.py
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import runner  # noqa: E402  (sibling module; path set up on the next line)

runner.bootstrap_paths()

from app.engine.engine import InsightEngine  # noqa: E402
from app.engine.envelope import AnswerEnvelope  # noqa: E402

# Floors chosen from the measured fake-provider baseline (all three sit at 1.00
# today). CI fails if a change drags any of them below these.
FLOORS = {
    "execution_accuracy": 0.90,
    "routing_accuracy": 0.90,
    "metric_selection_accuracy": 0.90,
}

# A relative tolerance for aggregate comparison; the fixture is exact, but a
# harness that only ever passes on an exact match is brittle to a rounding
# change in a metric expression.
REL_TOL = 0.005


# --------------------------------------------------------------------------- #
# golden case model                                                           #
# --------------------------------------------------------------------------- #
ValueCheck = Callable[[AnswerEnvelope], "tuple[bool, str]"]


@dataclass
class Case:
    question: str
    expected_route: str | None = None
    expected_metric: str | None = None
    expected_dims: list[str] = field(default_factory=list)
    value_check: ValueCheck | None = None
    value_desc: str = ""
    expected_abstain: bool = False


def _close(observed: float, expected: float) -> bool:
    if expected == 0:
        return abs(observed) <= 0.5
    return abs(observed - expected) <= max(0.5, abs(expected) * REL_TOL)


def _scalar(env: AnswerEnvelope) -> float | None:
    if not env.tables:
        return None
    rows = env.tables[0].rows
    if len(rows) == 1 and len(rows[0]) == 1:
        return float(rows[0][0])
    return None


def scalar_value(expected: float) -> ValueCheck:
    def check(env: AnswerEnvelope) -> tuple[bool, str]:
        got = _scalar(env)
        if got is None:
            return False, "no scalar"
        return _close(got, expected), f"{got:g} (want {expected:g})"

    return check


def assertion(fn: Callable[[float], bool], desc: str) -> ValueCheck:
    def check(env: AnswerEnvelope) -> tuple[bool, str]:
        got = _scalar(env)
        if got is None:
            return False, "no scalar"
        return fn(got), f"{got:g} ({desc})"

    return check


def _table_by_keyword(env: AnswerEnvelope, keyword: str):
    for table in env.tables:
        if keyword in table.title.lower():
            return table
    return None


def grouped_top(dim_keyword: str, label: str, value: float) -> ValueCheck:
    def check(env: AnswerEnvelope) -> tuple[bool, str]:
        table = _table_by_keyword(env, dim_keyword)
        if table is None or not table.rows:
            return False, f"no {dim_keyword} table"
        top = table.rows[0]
        ok = str(top[0]) == label and _close(float(top[-1]), value)
        return ok, f"{top[0]}={float(top[-1]):g} (want {label}={value:g})"

    return check


def change_check(current: float, prior: float, top_region: str, top_category: str) -> ValueCheck:
    def check(env: AnswerEnvelope) -> tuple[bool, str]:
        trend = env.tables[0] if env.tables else None
        if trend is None or len(trend.rows) < 2:
            return False, "no trend table"
        by_period = {str(r[0]): float(r[-1]) for r in trend.rows}
        cur = max(by_period)  # latest period label sorts last (e.g. 2026Q2)
        pri = min(by_period)
        ok_trend = _close(by_period[cur], current) and _close(by_period[pri], prior)
        ok_trend = ok_trend and by_period[cur] < by_period[pri]

        region_tbl = _table_by_keyword(env, "region")
        category_tbl = _table_by_keyword(env, "category")
        ok_region = bool(region_tbl and region_tbl.rows and str(region_tbl.rows[0][0]) == top_region)
        ok_cat = bool(
            category_tbl and category_tbl.rows and str(category_tbl.rows[0][0]) == top_category
        )
        got = f"{pri}->{cur}={by_period[pri]:g}->{by_period[cur]:g}, driver={top_region}/{top_category}"
        return (ok_trend and ok_region and ok_cat), got

    return check


def engine_abstained(env: AnswerEnvelope) -> bool:
    """Detect abstention defensively across whatever shape the engine grows.

    True if the engine declined rather than answering with fabricated numbers.
    Checked without assuming any not-yet-built field exists.
    """
    for attr in ("abstained", "abstain", "refused", "unsupported", "is_abstention"):
        if getattr(env, attr, False):
            return True
    if env.route == "clarify" or env.clarifying_question:
        return True
    text = " ".join([env.answer, *env.caveats]).lower()
    markers = (
        "cannot answer",
        "can't answer",
        "not a governed metric",
        "unknown metric",
        "no such metric",
        "not available",
        "unable to answer",
        "don't have",
        "do not have",
        "outside",
    )
    if any(m in text for m in markers):
        # Guard: a confident numeric answer with tables is not an abstention even
        # if the prose contains a hedge word.
        return not (env.tables and env.confidence != "low")
    return False


def detected_metric(env: AnswerEnvelope) -> str | None:
    """The metric the engine actually selected, read from the executed table.

    The query builder emits the metric name as the final output column, so the
    last column of the first table is the selected metric — inferred from real
    output rather than any engine-internal field.
    """
    if env.tables and env.tables[0].columns:
        return env.tables[0].columns[-1]
    return None


# --------------------------------------------------------------------------- #
# the golden set                                                              #
# --------------------------------------------------------------------------- #
def golden_cases() -> list[Case]:
    return [
        # --- change / hybrid (the planted North+Electronics decline story) ----
        Case(
            "Why did revenue decline last quarter?",
            expected_route="hybrid", expected_metric="revenue",
            value_check=change_check(1_152_000, 1_300_000, "North", "Electronics"),
            value_desc="Q2 1,152,000 < Q1 1,300,000; drivers North / Electronics",
        ),
        Case(
            "Why did sales decline last quarter?",
            expected_route="hybrid", expected_metric="revenue",
            value_check=change_check(1_152_000, 1_300_000, "North", "Electronics"),
            value_desc="'sales' resolves to revenue; same decline story",
        ),
        # --- scalar structured -------------------------------------------------
        Case(
            "What was revenue last quarter?",
            expected_route="structured", expected_metric="revenue",
            value_check=scalar_value(1_152_000), value_desc="revenue = 1,152,000",
        ),
        Case(
            "How many orders were placed last quarter?",
            expected_route="structured", expected_metric="orders",
            value_check=scalar_value(48), value_desc="orders = 48",
        ),
        Case(
            "What was the average order value last quarter?",
            expected_route="structured", expected_metric="avg_order_value",
            value_check=scalar_value(24_000), value_desc="AOV = 24,000",
        ),
        Case(
            "What were units sold last quarter?",
            expected_route="structured", expected_metric="units_sold",
            value_check=scalar_value(153), value_desc="units_sold = 153",
        ),
        Case(
            "How many units on hand do we have?",
            expected_route="structured", expected_metric="units_on_hand",
            value_check=scalar_value(5_580), value_desc="units_on_hand = 5,580",
        ),
        Case(
            "Which products should we restock?",
            expected_route="structured", expected_metric="units_on_hand",
            value_check=scalar_value(5_580), value_desc="restock -> inventory metric",
        ),
        # --- assertion-function checks (messy metrics; ranges, not equalities) -
        Case(
            "What was gross margin last quarter?",
            expected_route="structured", expected_metric="gross_margin",
            value_check=assertion(lambda v: 0 < v < 1_152_000, "0 < margin < revenue"),
            value_desc="gross margin positive and below revenue",
        ),
        Case(
            "What is the return rate last quarter?",
            expected_route="structured", expected_metric="return_rate",
            value_check=assertion(lambda v: 0.0 <= v <= 1.0, "rate in [0,1]"),
            value_desc="return rate is a valid proportion",
        ),
        # --- grouped structured ------------------------------------------------
        Case(
            "Show revenue by region last quarter.",
            expected_route="structured", expected_metric="revenue",
            expected_dims=["region"],
            value_check=grouped_top("region", "South", 313_600),
            value_desc="top region South = 313,600",
        ),
        Case(
            "Show revenue by category last quarter.",
            expected_route="structured", expected_metric="revenue",
            expected_dims=["category"],
            value_check=grouped_top("category", "Electronics", 501_600),
            value_desc="top category Electronics = 501,600",
        ),
        # --- unstructured (documents only) ------------------------------------
        Case(
            "Summarize customer complaints this month.",
            expected_route="unstructured", expected_metric=None,
            value_desc="pure document answer, no SQL",
        ),
        # --- abstention probes (should decline, not fabricate) ----------------
        Case(
            "What was our churn rate last quarter?",
            expected_route=None, expected_metric=None, expected_abstain=True,
            value_desc="churn is not a governed metric",
        ),
        Case(
            "What is the meaning of life?",
            expected_route=None, expected_metric=None, expected_abstain=True,
            value_desc="not an analytics question at all",
        ),
    ]


# --------------------------------------------------------------------------- #
# scoring                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class Scores:
    execution_accuracy: float
    routing_accuracy: float
    metric_selection_accuracy: float
    abstention_rate: float
    rows: list[list[object]]
    counts: dict[str, str]


def run_eval(engine: InsightEngine | None = None) -> Scores:
    engine = engine or InsightEngine.fixture()
    cases = golden_cases()

    exec_ok = exec_n = 0
    route_ok = route_n = 0
    metric_ok = metric_n = 0
    abst_ok = abst_n = 0
    rows: list[list[object]] = []

    for case in cases:
        env = engine.ask(case.question)

        route_res = ""
        if case.expected_route is not None:
            route_n += 1
            hit = env.route == case.expected_route
            route_ok += int(hit)
            route_res = f"{env.route} {'ok' if hit else 'X'}"
        elif case.expected_abstain:
            route_res = env.route

        metric_res = ""
        if case.expected_metric is not None:
            metric_n += 1
            got = detected_metric(env)
            hit = got == case.expected_metric
            metric_ok += int(hit)
            metric_res = f"{got} {'ok' if hit else 'X'}"

        exec_res = ""
        if case.value_check is not None:
            exec_n += 1
            hit, detail = case.value_check(env)
            exec_ok += int(hit)
            exec_res = f"{'ok' if hit else 'X'} {detail}"

        abst_res = ""
        if case.expected_abstain:
            abst_n += 1
            declined = engine_abstained(env)
            abst_ok += int(declined)
            abst_res = "abstained" if declined else "answered anyway"

        rows.append([
            _short(case.question), route_res or "-", metric_res or "-",
            exec_res or "-", abst_res or "-",
        ])

    return Scores(
        execution_accuracy=_ratio(exec_ok, exec_n),
        routing_accuracy=_ratio(route_ok, route_n),
        metric_selection_accuracy=_ratio(metric_ok, metric_n),
        abstention_rate=_ratio(abst_ok, abst_n),
        rows=rows,
        counts={
            "execution": f"{exec_ok}/{exec_n}",
            "routing": f"{route_ok}/{route_n}",
            "metric_selection": f"{metric_ok}/{metric_n}",
            "abstention": f"{abst_ok}/{abst_n}",
        },
    )


def _ratio(ok: int, n: int) -> float:
    return 1.0 if n == 0 else ok / n


def _short(text: str, width: int = 44) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def print_report(scores: Scores) -> None:
    runner.print_scoreboard(
        "Text-to-SQL golden set (fixture warehouse, fake provider)",
        ["question", "route", "metric", "execution", "abstention"],
        scores.rows,
    )
    runner.print_metrics(
        "Text-to-SQL scores",
        {
            "execution_accuracy": scores.execution_accuracy,
            "routing_accuracy": scores.routing_accuracy,
            "metric_selection_accuracy": scores.metric_selection_accuracy,
            "abstention_rate": scores.abstention_rate,
        },
        FLOORS,
    )
    print(
        f"\ncounts: execution {scores.counts['execution']}, "
        f"routing {scores.counts['routing']}, "
        f"metric-selection {scores.counts['metric_selection']}, "
        f"abstention {scores.counts['abstention']} "
        "(abstention is a reported probe, not a gate — see module docstring)."
    )
    runner.emit_results_json("text2sql", {
        "execution_accuracy": round(scores.execution_accuracy, 4),
        "routing_accuracy": round(scores.routing_accuracy, 4),
        "metric_selection_accuracy": round(scores.metric_selection_accuracy, 4),
        "abstention_rate": round(scores.abstention_rate, 4),
        "counts": scores.counts,
    })


# --------------------------------------------------------------------------- #
# pytest entry point                                                          #
# --------------------------------------------------------------------------- #
def test_text2sql_eval() -> None:
    scores = run_eval()
    assert scores.execution_accuracy >= FLOORS["execution_accuracy"], (
        f"execution accuracy {scores.execution_accuracy:.3f} below floor "
        f"{FLOORS['execution_accuracy']} ({scores.counts['execution']})"
    )
    assert scores.routing_accuracy >= FLOORS["routing_accuracy"], (
        f"routing accuracy {scores.routing_accuracy:.3f} below floor "
        f"{FLOORS['routing_accuracy']} ({scores.counts['routing']})"
    )
    assert scores.metric_selection_accuracy >= FLOORS["metric_selection_accuracy"], (
        f"metric-selection accuracy {scores.metric_selection_accuracy:.3f} below floor "
        f"{FLOORS['metric_selection_accuracy']} ({scores.counts['metric_selection']})"
    )
    # Abstention is measured but not gated while the engine's abstention path is
    # still being built; a valid ratio is all we assert here.
    assert 0.0 <= scores.abstention_rate <= 1.0


if __name__ == "__main__":
    print_report(run_eval())
