"""InsightGPT ingestion: connectors, redaction, and the raw Postgres loader.

Implements the extract -> redact -> load-raw flow from ``docs/03-ingestion-etl``:
a small ``Connector`` interface (records or documents), a redaction pass that
runs once before anything is persisted, and an idempotent delete-then-write
loader into the Postgres ``raw`` schema with content-hash incrementality. The
loader degrades cleanly to a no-op with a clear message when psycopg / Postgres
is unavailable, so the rest of the pipeline stays runnable offline.
"""

from __future__ import annotations

from .connectors.base import Connector, Document, Record, SourceUnit
from .redact import redact_record, redact_text

__all__ = [
    "Connector",
    "Document",
    "Record",
    "SourceUnit",
    "redact_record",
    "redact_text",
]
