"""``python -m services.ingestion run --job full_ingest --source all``."""

from __future__ import annotations

from .run import main

if __name__ == "__main__":
    raise SystemExit(main())
