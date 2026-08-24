"""Document / folder connector (``docs/03`` §3.2, ``kind="documents"``).

Walks a folder of tickets / reviews / reports, reusing rememory's discovery
discipline: prune ignored directories, **never index credential files**, reject
by content probe (NUL byte / non-UTF-8) rather than extension alone, skip empty
and oversized files, and hash bytes for change detection. Each document is
yielded as text plus structured metadata (``doc_type``, ``product_id``,
``region``, ``created_ts``) so retrieval can filter it later.

Two input shapes are supported so it works on the generator's output and on a
plain drop-folder of text:

* a ``.json`` file holding a **list** of document dicts -> one ``Document`` per
  element (the generator writes ``support_tickets.json`` etc. this way);
* any other UTF-8 text file (``.txt`` / ``.md``) -> a single ``Document``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

from .base import Document, SourceUnit

# Filenames/extensions that are credentials — never landed, never embedded.
_CREDENTIAL_NAMES = {".env", "credentials.json", "id_rsa", "id_dsa", ".htpasswd"}
_CREDENTIAL_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".keystore"}
_CREDENTIAL_STEMS = ("secret", "credential", "password")

_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target"}
_TEXT_SUFFIXES = {".json", ".txt", ".md", ".markdown", ".log"}
_MAX_BYTES = 5_000_000  # skip anything larger than ~5 MB


class DocumentConnector:
    kind = "documents"

    def __init__(self, name: str, root: str | Path, max_bytes: int = _MAX_BYTES):
        self.name = name
        self._root = Path(root)
        self._max_bytes = max_bytes
        # Populated during discover(); records why units were skipped (honest
        # reporting, mirroring rememory's --explain).
        self.skipped: dict[str, str] = {}

    def discover(self) -> list[SourceUnit]:
        self.skipped = {}
        units: list[SourceUnit] = []
        for path in self._walk():
            reason = self._reject_reason(path)
            if reason:
                self.skipped[str(path)] = reason
                continue
            units.append(
                SourceUnit(
                    source=self.name,
                    unit_id=str(path.relative_to(self._root)).replace("\\", "/"),
                    fingerprint=_sha256_file(path),
                )
            )
        return units

    def fingerprint(self, unit: SourceUnit) -> str:
        return _sha256_file(self._root / unit.unit_id)

    def extract(self, unit: SourceUnit) -> Iterator[Document]:
        path = self._root / unit.unit_id
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            payload = json.loads(text)
            if isinstance(payload, list):
                for item in payload:
                    yield self._document_from_dict(item)
                return
            if isinstance(payload, dict):
                yield self._document_from_dict(payload)
                return
        # Plain text file: one document, metadata carries only its path/type.
        yield Document(
            doc_id=unit.unit_id,
            text=text,
            metadata={"doc_type": "text", "source_path": unit.unit_id},
        )

    # ---- internals -----------------------------------------------------------
    def _walk(self) -> Iterator[Path]:
        for path in sorted(self._root.rglob("*")):
            if path.is_dir():
                continue
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            yield path

    def _reject_reason(self, path: Path) -> str | None:
        if _is_credential(path):
            return "credential file (never indexed)"
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            return "unsupported extension"
        try:
            size = path.stat().st_size
        except OSError:
            return "unreadable"
        if size == 0:
            return "empty"
        if size > self._max_bytes:
            return "oversized"
        head = path.read_bytes()[:2048]
        if b"\x00" in head:
            return "binary (NUL byte)"
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            return "non-utf-8"
        return None

    @staticmethod
    def _document_from_dict(item: dict) -> Document:
        doc_id = str(item.get("doc_id") or item.get("id") or item.get("ticket_id") or "")
        # Body/title become the retrievable text; the rest is filter metadata.
        title = str(item.get("title") or "")
        body = str(item.get("body") or "")
        text = f"{title}\n\n{body}".strip() if title else body
        metadata = {
            k: v
            for k, v in item.items()
            if k not in {"title", "body"} and v is not None
        }
        return Document(doc_id=doc_id, text=text, metadata=metadata)


def _is_credential(path: Path) -> bool:
    name = path.name.lower()
    if name in _CREDENTIAL_NAMES:
        return True
    if path.suffix.lower() in _CREDENTIAL_SUFFIXES:
        return True
    stem = path.stem.lower()
    return any(marker in stem for marker in _CREDENTIAL_STEMS)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
