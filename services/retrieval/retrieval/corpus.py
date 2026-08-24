"""The ingestion -> retrieval hand-off: load a corpus, index only what changed.

``services/ingestion`` redacts the generated documents and writes them to one
canonical file (``data/ingested/documents.json`` by default). This module is the
consuming half of that contract:

* :func:`load_corpus` reads that file — or a folder / JSON file of documents —
  and normalizes every producer spelling through :mod:`retrieval.schema`;
* :class:`IndexState` remembers the ``_content_hash`` of every document the last
  successful index wrote, so a re-index re-embeds only documents whose content
  actually changed and *deletes* documents that disappeared from the corpus.

Changed-only matters because embedding is the expensive step: a nightly reindex
over an unchanged 644-document corpus should cost close to nothing, and a
document removed upstream must not linger in the collection forever.

The state file lives next to the corpus and is keyed by collection name, so
pointing the same corpus at a second collection re-indexes it in full rather
than silently skipping everything.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import Document
from .schema import CONTENT_HASH_KEY

# Where `services/ingestion` writes its redacted corpus by default. Resolved
# from this file: services/retrieval/retrieval/corpus.py -> repo root is 3 up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS_PATH = _REPO_ROOT / "data" / "ingested" / "documents.json"

STATE_FILENAME = ".index_state.json"
STATE_VERSION = 1


def default_corpus_path() -> Path:
    """Where the ingestion corpus lives.

    ``DOCUMENT_CORPUS_PATH`` overrides it — the same variable
    ``IngestionSettings`` and ``WorkerSettings`` read, so all three sides of the
    hand-off can be relocated together with one setting.
    """
    if override := os.environ.get("DOCUMENT_CORPUS_PATH"):
        return Path(override)
    return DEFAULT_CORPUS_PATH


def load_corpus(path: Path) -> list[Document]:
    """Load documents from a JSON file (a list) or a folder of JSON files.

    A folder is read non-recursively, sorted by name, so ordering is
    deterministic. Every record goes through the canonical normalizer, so a
    generator document (``doc_type`` / ``created_ts`` / ``support_agent``) and a
    sample document (``source_type`` / ``date`` / ``agent``) become the same
    shape.
    """
    raws: list[dict] = []
    if path.is_dir():
        for f in sorted(path.glob("*.json")):
            if f.name == STATE_FILENAME:
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            raws.extend(data if isinstance(data, list) else [data])
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        raws.extend(data if isinstance(data, list) else [data])
    docs = [Document.from_dict(r) for r in raws]
    # Stable order so a re-index touches documents in the same sequence and an
    # interrupted run resumes predictably.
    docs.sort(key=lambda d: d.doc_id)
    return docs


@dataclass
class ChangeSet:
    """What a changed-only re-index should actually do."""

    to_index: list[Document] = field(default_factory=list)
    unchanged: int = 0
    removed: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.to_index) + self.unchanged


class IndexState:
    """Persisted ``doc_id -> content_hash`` map for one collection.

    Absent or unreadable state is treated as "nothing indexed yet" — the safe
    direction, since that re-indexes rather than skipping.
    """

    def __init__(self, path: Path, collection: str) -> None:
        self.path = Path(path)
        self.collection = collection
        self.hashes: dict[str, str] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            return
        if raw.get("collection") != self.collection:
            # A different collection was indexed from this corpus; its hashes
            # say nothing about what THIS collection contains.
            return
        hashes = raw.get("documents")
        if isinstance(hashes, dict):
            self.hashes = {str(k): str(v) for k, v in hashes.items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "collection": self.collection,
            "documents": dict(sorted(self.hashes.items())),
        }
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )

    # -- diffing -----------------------------------------------------------
    def plan(self, docs: list[Document], *, full: bool = False) -> ChangeSet:
        """Split the corpus into re-index / skip, and list vanished doc ids."""
        current = {d.doc_id: (d.content_hash or "") for d in docs}
        removed = [doc_id for doc_id in sorted(self.hashes) if doc_id not in current]
        if full:
            return ChangeSet(to_index=list(docs), unchanged=0, removed=removed)
        changeset = ChangeSet(removed=removed)
        for doc in docs:
            if self.hashes.get(doc.doc_id) == (doc.content_hash or ""):
                changeset.unchanged += 1
            else:
                changeset.to_index.append(doc)
        return changeset

    def record(self, docs: list[Document]) -> None:
        for doc in docs:
            self.hashes[doc.doc_id] = doc.content_hash or ""

    def forget(self, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self.hashes.pop(doc_id, None)


def state_path_for(corpus_path: Path) -> Path:
    """State lives beside the corpus (in it, when the corpus is a folder)."""
    corpus_path = Path(corpus_path)
    parent = corpus_path if corpus_path.is_dir() else corpus_path.parent
    return parent / STATE_FILENAME


__all__ = [
    "CONTENT_HASH_KEY",
    "DEFAULT_CORPUS_PATH",
    "STATE_FILENAME",
    "ChangeSet",
    "IndexState",
    "default_corpus_path",
    "load_corpus",
    "state_path_for",
]
