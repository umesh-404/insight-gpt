"""Tiny shared scaffolding for the eval harnesses: path bootstrap + a printer.

Deliberately dependency-free (stdlib only) so it runs under any of the package
venvs. It never writes files — results are printed, including a single machine
-readable ``RESULTS_JSON:`` line per harness so a CI log can be scraped without
a build artifact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def bootstrap_paths() -> None:
    """Put ``services/api`` (the ``app`` package) and this dir on ``sys.path``.

    Called at import time by each harness so ``python tests/eval/<name>.py`` and
    ``pytest tests/eval`` both resolve ``app.*`` and a sibling ``import runner``.
    """
    here = Path(__file__).resolve().parent
    api_dir = here.parents[1] / "services" / "api"
    for candidate in (str(api_dir), str(here)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def print_scoreboard(title: str, headers: list[str], rows: list[list[object]]) -> None:
    """Print a plain-text table. ASCII only, so it survives a cp1252 console."""
    cols = [headers, *[[_cell(c) for c in r] for r in rows]]
    widths = [max(len(str(row[i])) for row in cols) for i in range(len(headers))]
    line = "+".join("-" * (w + 2) for w in widths)
    print(f"\n{title}")
    print(f"+{line}+")
    print("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |")
    print(f"+{line}+")
    for r in rows:
        cells = [_cell(c) for c in r]
        print("| " + " | ".join(cells[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    print(f"+{line}+")


def print_metrics(title: str, metrics: dict[str, float | int], floors: dict[str, float]) -> None:
    """Print a metric block with PASS/FAIL against each floor."""
    rows = []
    for name, value in metrics.items():
        floor = floors.get(name)
        verdict = "-" if floor is None else ("PASS" if value >= floor else "FAIL")
        floor_s = "-" if floor is None else f"{floor:.2f}"
        rows.append([name, _num(value), floor_s, verdict])
    print_scoreboard(title, ["metric", "score", "floor", "status"], rows)


def emit_results_json(name: str, payload: dict) -> None:
    """One-line JSON dump so a log scraper can track metrics over time."""
    print(f"RESULTS_JSON: {json.dumps({'harness': name, **payload}, default=str)}")


def _num(value: float | int) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _cell(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)
