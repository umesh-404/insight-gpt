"""Source connectors: one small interface, one class per source (docs/03 §3)."""

from __future__ import annotations

from .base import Connector, Document, Record, SourceUnit
from .csv_connector import CSVConnector
from .document_connector import DocumentConnector

__all__ = [
    "CSVConnector",
    "Connector",
    "Document",
    "DocumentConnector",
    "Record",
    "SourceUnit",
]
