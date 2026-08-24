"""Tiny CLI to exercise the engine offline: ``python -m app.cli "question"``.

Uses the fixture warehouse + the offline provider by default, so it runs with no
external services. Set ``LLM_PROVIDER`` to use a real model.
"""

from __future__ import annotations

import json
import sys

from .engine.engine import InsightEngine
from .providers.factory import get_provider


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    question = " ".join(argv) or "Why did sales decline last quarter?"
    provider = get_provider()  # LLM_PROVIDER env, defaults to ollama; use fake offline
    engine = InsightEngine.fixture(provider=provider)
    envelope = engine.ask(question)
    print(json.dumps(envelope.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
