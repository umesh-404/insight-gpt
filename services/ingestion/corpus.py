"""Publishing the redacted document corpus (``docs/03`` §7).

The document branch of ingestion does not embed anything — that is retrieval's
job. What it owes retrieval is a single file it can point at: every document,
already redacted, each stamped with a content hash so the indexer can re-embed
only what changed.

That file (``data/ingested/documents.json`` by default) is the hand-off
contract. It is written **atomically** and only when its contents actually
changed, so a re-run is a true no-op — no mtime churn, no half-written file for
a concurrently-running indexer to read.

Field names are left exactly as the producer wrote them (``doc_type``,
``created_ts``, ``author_role: support_agent``, ...). Mapping those onto the
canonical retrieval schema is owned by ``retrieval.schema``, the consumer, so
there is exactly one normalizer in the repo rather than two that can drift.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

# Key the per-document hash is stamped under. This is ingestion's OWN hash: it
# decides whether the corpus file needs rewriting and gives `incremental_sync` an
# honest changed count. Retrieval recomputes its own hash over the canonical
# fields it embeds, so neither side depends on the other's algorithm.
CONTENT_HASH_KEY = "_content_hash"

# The fields whose values decide whether a document must be re-embedded. Aliases
# are listed alongside their canonical name so a producer using either spelling
# hashes to the same value.
_HASHED_FIELDS: tuple[tuple[str, ...], ...] = (
    ("doc_id", "id", "ticket_id", "review_id", "report_id"),
    ("source_type", "doc_type", "type"),
    ("title",),
    ("body", "text"),
    ("created_at", "created_ts", "date", "timestamp"),
    ("region",),
    ("category",),
    ("product_ref", "product_sku", "sku", "product_id"),
    ("order_ref", "order_id"),
    ("author_role",),
    ("channel",),
)


@dataclass
class CorpusResult:
    path: Path
    documents: int
    changed: int
    written: bool


def _first(raw: dict, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return None


def content_hash(raw: dict) -> str:
    """Hash the index-relevant content of one document (producer side)."""
    payload = {
        keys[0]: (lambda v: None if v is None else str(v))(_first(raw, keys))
        for keys in _HASHED_FIELDS
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def stamp(raw: dict) -> dict:
    """Return the document with its content hash attached."""
    doc = dict(raw)
    doc[CONTENT_HASH_KEY] = content_hash(raw)
    return doc


def _serialize(docs: list[dict]) -> str:
    # Sorted by doc_id so the file is a deterministic function of its contents:
    # the same corpus always produces byte-identical output.
    ordered = sorted(docs, key=lambda d: str(d.get("doc_id", "")))
    return json.dumps(ordered, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def previous_hashes(path: Path) -> dict[str, str]:
    """The ``doc_id -> content_hash`` map of the corpus already on disk."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    return {
        str(d.get("doc_id", "")): str(d.get(CONTENT_HASH_KEY, ""))
        for d in data
        if isinstance(d, dict)
    }


def publish(path: Path, docs: list[dict]) -> CorpusResult:
    """Write the stamped corpus atomically, only if it changed."""
    path = Path(path)
    stamped = [stamp(d) for d in docs]
    before = previous_hashes(path)
    changed = sum(
        1
        for d in stamped
        if before.get(str(d.get("doc_id", ""))) != d[CONTENT_HASH_KEY]
    )
    payload = _serialize(stamped)

    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return CorpusResult(path=path, documents=len(stamped), changed=0, written=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX and Windows
    return CorpusResult(path=path, documents=len(stamped), changed=changed, written=True)
