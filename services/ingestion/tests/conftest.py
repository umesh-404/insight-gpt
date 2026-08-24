"""Make ``services/ingestion`` importable as the top-level ``ingestion`` package
under pytest, regardless of where pytest is invoked from."""

from __future__ import annotations

import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT.parent))
