"""CSV / Excel-export connector (``docs/03`` §3.2).

Points at a directory (or an explicit set of files). Each file is a
``SourceUnit`` whose fingerprint is the SHA-256 of its bytes (rememory's
content-hash idea). The header row becomes column names and ``extract`` yields
one dict ``Record`` per data row. Handles the synthetic exports of customers,
products, stores, orders, order_items, inventory.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from pathlib import Path

from .base import Record, SourceUnit


class CSVConnector:
    kind = "records"

    def __init__(self, name: str, root: str | Path, pattern: str = "*.csv"):
        self.name = name
        self._root = Path(root)
        self._pattern = pattern

    def _files(self) -> list[Path]:
        if self._root.is_file():
            return [self._root]
        return sorted(self._root.glob(self._pattern))

    def discover(self) -> list[SourceUnit]:
        units: list[SourceUnit] = []
        for path in self._files():
            digest = _sha256_file(path)
            units.append(
                SourceUnit(
                    source=self.name,
                    unit_id=path.stem,  # table name = file stem (e.g. "orders")
                    fingerprint=digest,
                    updated_at=None,
                )
            )
        return units

    def fingerprint(self, unit: SourceUnit) -> str:
        path = self._path_for(unit)
        return _sha256_file(path)

    def extract(self, unit: SourceUnit) -> Iterator[Record]:
        path = self._path_for(unit)
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Land loosely: everything is text in raw (docs/02 §3), staging
                # casts. Empty strings become NULLs downstream.
                yield dict(row)

    def _path_for(self, unit: SourceUnit) -> Path:
        if self._root.is_file():
            return self._root
        matches = [p for p in self._files() if p.stem == unit.unit_id]
        if not matches:
            raise FileNotFoundError(f"no CSV for unit {unit.unit_id!r} under {self._root}")
        return matches[0]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
