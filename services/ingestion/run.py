"""Ingestion orchestration: extract -> redact -> load raw (``docs/03`` §2, §5).

Wires the connectors, the single redaction pass, and the raw loader into one
re-runnable job and tracks the same honest counters rememory surfaces
(``units_seen``, ``units_skipped``, ``rows_loaded``, ``secrets_redacted``) — the
per-run stats docs/03 §5.3 lifts into ``pipeline_runs``.

Runs offline: with no Postgres the record branch redacts and reports
``skipped_unavailable`` per unit, while the document branch still redacts and
counts, so the flow is demonstrable end to end without a database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import uuid
from dataclasses import dataclass, field

from .config import IngestionSettings, get_settings
from .connectors.base import Document
from .connectors.csv_connector import CSVConnector
from .connectors.document_connector import DocumentConnector
from .loader import RawLoader
from .redact import redact_record, redact_text

# Record sources that carry PII columns needing redaction before landing raw.
_PII_SOURCES = {"customers"}


@dataclass
class RunStats:
    job_type: str
    source: str
    batch_id: str
    started_at: str
    status: str = "running"
    units_seen: int = 0
    units_loaded: int = 0
    units_skipped: int = 0
    rows_loaded: int = 0
    secrets_redacted: int = 0
    documents_redacted: int = 0
    finished_at: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__


def run_records_ingest(
    connector: CSVConnector, loader: RawLoader, stats: RunStats, force: bool
) -> None:
    for unit in connector.discover():
        stats.units_seen += 1
        rows = list(connector.extract(unit))
        columns = list(rows[0].keys()) if rows else []
        redact = unit.unit_id in _PII_SOURCES
        if redact:
            redacted_rows = []
            for row in rows:
                clean, n = redact_record(row)
                stats.secrets_redacted += n
                redacted_rows.append(clean)
            rows = redacted_rows
        result = loader.load_records(
            source=connector.name,
            unit_id=unit.unit_id,
            columns=columns,
            rows=rows,
            fingerprint=unit.fingerprint,
            batch_id=stats.batch_id,
            force=force,
        )
        if result.status == "loaded":
            stats.units_loaded += 1
            stats.rows_loaded += result.rows_loaded
        else:
            stats.units_skipped += 1
            stats.notes.append(f"{unit.unit_id}: {result.status} ({result.message})")


def run_documents_ingest(connector: DocumentConnector, stats: RunStats) -> list[Document]:
    """Extract + redact documents. Handing them to retrieval/Qdrant is owned by
    ``services/retrieval`` (out of scope here); we return the redacted docs and
    count redactions so the branch is demonstrable."""
    redacted: list[Document] = []
    for unit in connector.discover():
        stats.units_seen += 1
        for doc in connector.extract(unit):
            result = redact_text(doc.text)
            stats.secrets_redacted += result.count
            stats.documents_redacted += 1
            # Metadata may hold contact strings too (e.g. a body echoed a phone).
            clean_meta, meta_n = redact_record(doc.metadata)
            stats.secrets_redacted += meta_n
            redacted.append(
                Document(doc_id=doc.doc_id, text=result.text, metadata=clean_meta)
            )
    for path, reason in connector.skipped.items():
        stats.notes.append(f"skipped {path}: {reason}")
    return redacted


def run(job: str, source: str, settings: IngestionSettings | None = None) -> RunStats:
    settings = settings or get_settings()
    loader = RawLoader(settings.postgres_dsn, schema=settings.raw_schema)
    force = job == "full_ingest"
    stats = RunStats(
        job_type=job,
        source=source,
        batch_id=uuid.uuid4().hex[:12],
        started_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    if not loader.available:
        stats.notes.append(f"loader unavailable: {loader.unavailable_reason()}")

    if source in ("all", "csv"):
        csv_conn = CSVConnector("retail_csv", settings.generated_dir)
        run_records_ingest(csv_conn, loader, stats, force)
    if source in ("all", "documents"):
        doc_conn = DocumentConnector("retail_docs", settings.generated_dir / "documents")
        run_documents_ingest(doc_conn, stats)

    stats.finished_at = dt.datetime.now(dt.UTC).isoformat()
    stats.status = "success" if loader.available or source == "documents" else "partial"
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.ingestion", description=__doc__)
    parser.add_argument("action", choices=["run"], help="what to do")
    parser.add_argument(
        "--job",
        default="full_ingest",
        choices=["full_ingest", "incremental_sync"],
        help="full reload vs content-hash incremental",
    )
    parser.add_argument(
        "--source", default="all", choices=["all", "csv", "documents"], help="source to ingest"
    )
    args = parser.parse_args(argv)

    stats = run(args.job, args.source)
    print(f"[ingestion] job={stats.job_type} source={stats.source} status={stats.status}")
    print(f"  units_seen={stats.units_seen} loaded={stats.units_loaded} "
          f"skipped={stats.units_skipped} rows_loaded={stats.rows_loaded}")
    print(f"  secrets_redacted={stats.secrets_redacted} "
          f"documents_redacted={stats.documents_redacted}")
    for note in stats.notes[:12]:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
