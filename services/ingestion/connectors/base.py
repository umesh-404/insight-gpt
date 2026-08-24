"""The connector interface (``docs/03-ingestion-etl.md`` §3.1).

Every source implements one small ``Connector`` protocol, so adding a source is
adding a class, not editing the pipeline. ``discover()`` is split from
``extract()`` so we can decide *whether* a unit changed (via a cheap fingerprint)
before paying to read its full payload — mirroring rememory's cheapest-first
discovery.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Record = dict[str, object]


@dataclass(frozen=True)
class SourceUnit:
    """A cheaply-listable unit of extraction (a file, a table, a folder entry)."""

    source: str
    unit_id: str
    fingerprint: str
    updated_at: str | None = None


@dataclass
class Document:
    """A redacted-later free-text document plus filter metadata for retrieval."""

    doc_id: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    name: str  # stable source id, used as the raw table prefix
    kind: Literal["records", "documents"]

    def discover(self) -> list[SourceUnit]:
        """Cheap listing of extractable units with a fingerprint, no full read."""
        ...

    def extract(self, unit: SourceUnit) -> Iterator[Record] | Iterator[Document]:
        """Yield records or documents for one unit."""
        ...

    def fingerprint(self, unit: SourceUnit) -> str:
        """Content hash / watermark used to skip unchanged units."""
        ...
