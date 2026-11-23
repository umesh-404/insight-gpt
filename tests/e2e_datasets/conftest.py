"""Path + environment setup shared by every module in this package.

``app`` lives in ``services/api``, which is not installed into the repo-root
environment, so the path is bootstrapped the same way ``tests/eval/runner.py``
does it. The environment defaults are set at import time, before anything
imports ``app.config``, because the settings object reads them once.

Nothing here starts a service or writes outside ``tmp_path``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _bootstrap_paths() -> None:
    for candidate in (REPO_ROOT / "services" / "api", REPO_ROOT / "services" / "retrieval"):
        path = str(candidate)
        if candidate.is_dir() and path not in sys.path:
            sys.path.insert(0, path)


_bootstrap_paths()

# The offline, deterministic stack. ``setdefault`` so a deliberate override on
# the command line (a live run, say) still wins.
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("WAREHOUSE", "duckdb")
os.environ.setdefault("RETRIEVER", "fixture")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-32-bytes")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
