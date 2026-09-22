"""Offline evaluation harnesses for the InsightGPT insight engine.

Two harnesses live here and both run against the deterministic fixture stack
(``InsightEngine.fixture()``: the ``fake`` provider + the in-process DuckDB
warehouse), so they measure real engine behaviour with no models, database, or
network:

* :mod:`tests.eval.text2sql` — grounded text-to-SQL execution accuracy, routing
  accuracy, metric-selection accuracy, and an abstention probe.
* :mod:`tests.eval.faithfulness` — RAG groundedness, citation coverage, and a
  no-fabricated-number check.

Each module is runnable two ways: ``python tests/eval/<name>.py`` prints a
scoreboard, and ``pytest tests/eval/<name>.py`` asserts score floors so CI
catches regressions. See ``docs/10-testing-eval.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``app`` (the API package) and this directory (for a sibling ``import
# runner``) importable no matter how the harness is invoked — as a script, as a
# pytest module, or imported as this package.
_HERE = Path(__file__).resolve().parent
_API_DIR = _HERE.parents[1] / "services" / "api"
for _candidate in (str(_API_DIR), str(_HERE)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)
