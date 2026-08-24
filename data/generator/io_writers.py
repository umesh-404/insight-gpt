"""Output writers: CSV (stdlib), JSON documents, optional parquet.

CSV uses only the standard library so the generator runs in a clean environment
with no third-party data deps. Parquet is best-effort: emitted only when
``pyarrow`` is importable, otherwise skipped with a message (never an error).
"""

from __future__ import annotations

import csv
import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _rows(records: Sequence[Any], columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in records:
        d = dataclasses.asdict(r) if dataclasses.is_dataclass(r) else dict(r)
        out.append({c: d.get(c) for c in columns})
    return out


def write_csv(path: Path, records: Sequence[Any], columns: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in _rows(records, columns):
            writer.writerow(row)
    return len(records)


def write_parquet(path: Path, records: Sequence[Any], columns: list[str]) -> bool:
    """Write parquet if pyarrow is available. Returns False (with a note) if not."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False
    rows = _rows(records, columns)
    table = pa.Table.from_pylist(rows) if rows else pa.table({c: [] for c in columns})
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return True


def write_json(path: Path, records: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(records)
