"""Pin the deployment selectors so the API suite is hermetic.

``app/config.py`` fills missing settings from the repo-root ``.env`` so a
host-run API needs no per-process environment. That is right for running the
app and wrong for running the tests: the suite then inherits whatever the
developer configured, and a `.env` set up for a real demo (``RETRIEVER=qdrant``,
``LLM_PROVIDER=gemini``) turns offline unit tests into live-service tests that
reach for Qdrant, Ollama and a cloud key.

Individual test modules already do ``os.environ.setdefault("LLM_PROVIDER",
"fake")`` at import time for exactly this reason. Doing it here instead makes it
apply to every module including the ones that forgot, and — because pytest
imports ``conftest.py`` before any test module, and therefore before
``app.config`` reads ``.env`` — it wins regardless of import order. The previous
arrangement only worked in a full-suite run, where some *other* module's
``setdefault`` happened to run first; ``pytest tests/test_build.py`` on its own
failed.

``setdefault``, not assignment: CI, a developer, or a test that wants a
different value still overrides it. Tests that need the real selectors pass
them explicitly (``Settings(retriever="qdrant")``) rather than relying on the
environment, which is what keeps them deterministic.
"""

from __future__ import annotations

import os

# The offline stack: deterministic, no network, no model server, no key.
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("RETRIEVER", "fixture")
os.environ.setdefault("WAREHOUSE", "duckdb")
