"""In-process registry and side-effect helpers for uploaded datasets.

The registry itself mirrors ``app.api.routers.reports`` — a bounded dict plus a
``reset_state()`` for tests — with one addition: indexing runs on a background
worker thread, so every read and write goes through a lock.

This module also owns the three things the router must not reimplement:

* **Raw-file persistence** (``save_upload``). The uploaded bytes are kept under
  ``data/ingested/<dataset_id>/`` so a re-index never needs a re-upload.
* **Redaction** (:func:`redact`). Secrets and PII are removed *before* any text
  is handed to the indexer, and the count is reported on the dataset
  (``docs/04`` §2.4, contract §6.4). The implementation is not duplicated here:
  it is loaded from the sibling retrieval package — the same module the indexer
  itself uses, so the two can never drift. If no implementation can be loaded
  the dataset *fails*; it is never indexed unredacted.
* **The seam into the indexing layer** (:func:`index_dataset_text`,
  :func:`delete_dataset_points`). Imported lazily so the default offline API
  image and its test suite never need the retrieval package or a Qdrant client
  installed, exactly as ``app/engine/build.py`` does for the retriever.

Safety note (contract §3): the Qdrant instance is shared with another project.
Nothing in here drops, recreates or prunes a collection. Deletion is a filtered
delete on ``dataset_id`` performed by the retrieval layer, and
:func:`delete_dataset_points` refuses to call it unless the configured
collection name is one of ours (``insightgpt_`` prefix).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

log = logging.getLogger("insightgpt.datasets")

#: Collections InsightGPT is allowed to write to or delete from. The shared
#: instance also holds another project's ``code`` / ``docs`` / ``memory``, and a
#: single mistaken delete there is unrecoverable, so the prefix is asserted in
#: code before any delete call rather than trusted to configuration.
COLLECTION_PREFIX = "insightgpt_"

MAX_DATASETS = 500

DatasetStatus = Literal["pending", "indexing", "ready", "failed"]
DatasetKind = Literal["document", "tabular"]


# --------------------------------------------------------------------------
# Wire shapes (contract §4 — frozen; the web client is built against these)
# --------------------------------------------------------------------------
class Dataset(BaseModel):
    id: str
    name: str
    kind: DatasetKind
    status: DatasetStatus
    bytes: int
    chunks: int = 0
    redactions: int = 0
    created_at: datetime
    error: str | None = None


class DatasetJob(BaseModel):
    dataset_id: str
    status: DatasetStatus
    progress: float = 0.0
    message: str | None = None


class StoredDataset(Dataset):
    """What the registry keeps. Extra fields never reach the wire: the routes
    declare ``response_model=Dataset``, which drops them on serialization."""

    progress: float = 0.0
    message: str | None = None
    #: Absolute path of the persisted raw upload, for a re-index without a
    #: re-upload. Not exposed — a server path is not the client's business.
    path: str | None = None


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
_LOCK = threading.Lock()
_DATASETS: dict[str, StoredDataset] = {}
_DATA_DIR: Path | None = None


def _repo_root() -> Path:
    """The repository root, found by walking up to the directory holding
    ``services/``. Used only to locate ``data/`` and the sibling source tree."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "services").is_dir():
            return parent
    return here.parents[4]


def data_dir() -> Path:
    """Where raw uploads are persisted (``<repo>/data/ingested`` by default)."""
    if _DATA_DIR is not None:
        return _DATA_DIR
    override = os.getenv("DATASETS_DIR")
    if override:
        return Path(override)
    return _repo_root() / "data" / "ingested"


def set_data_dir(path: str | Path | None) -> None:
    """Point persistence at another directory (tests)."""
    global _DATA_DIR
    _DATA_DIR = Path(path) if path is not None else None


def reset_state() -> None:
    """Clear the registry (tests). Does not touch files on disk."""
    with _LOCK:
        _DATASETS.clear()


def put(record: StoredDataset) -> None:
    with _LOCK:
        _DATASETS[record.id] = record
        _trim_locked()


def get(dataset_id: str) -> StoredDataset | None:
    with _LOCK:
        return _DATASETS.get(dataset_id)


def list_all() -> list[StoredDataset]:
    with _LOCK:
        return sorted(_DATASETS.values(), key=lambda d: d.created_at, reverse=True)


def remove(dataset_id: str) -> StoredDataset | None:
    with _LOCK:
        return _DATASETS.pop(dataset_id, None)


def update(dataset_id: str, **fields: Any) -> StoredDataset | None:
    """Patch a stored record in place under the lock. Unknown ids are ignored —
    a dataset deleted mid-index must not be resurrected by its own worker."""
    with _LOCK:
        record = _DATASETS.get(dataset_id)
        if record is None:
            return None
        updated = record.model_copy(update=fields)
        _DATASETS[dataset_id] = updated
        return updated


def _trim_locked() -> None:
    if len(_DATASETS) <= MAX_DATASETS:
        return
    oldest = sorted(_DATASETS, key=lambda i: _DATASETS[i].created_at)
    for dataset_id in oldest[: len(_DATASETS) - MAX_DATASETS]:
        _DATASETS.pop(dataset_id, None)


def job_of(record: StoredDataset) -> DatasetJob:
    return DatasetJob(
        dataset_id=record.id,
        status=record.status,
        progress=record.progress,
        message=record.message,
    )


# --------------------------------------------------------------------------
# Raw-file persistence
# --------------------------------------------------------------------------
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """A filesystem-safe basename. Strips directories, so a multipart filename
    of ``../../.env`` lands as ``.env`` inside the dataset's own folder."""
    stem = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", stem).strip("._") or "upload"
    return cleaned[:120]


def save_upload(dataset_id: str, filename: str, payload: bytes) -> Path:
    """Persist the raw bytes under ``data/ingested/<dataset_id>/``."""
    folder = data_dir() / dataset_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_filename(filename)
    target.write_bytes(payload)
    return target


def remove_files(dataset_id: str) -> bool:
    """Delete the dataset's stored folder. True if anything was removed."""
    folder = data_dir() / dataset_id
    if not folder.exists():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


# --------------------------------------------------------------------------
# Redaction — shared implementation, never a second copy
# --------------------------------------------------------------------------
_REDACTOR: Any = None


def _load_from_path(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_redactor() -> Any:
    """Find the shared redactor.

    Preference order, and why:

    1. ``retrieval.redact`` as an installed package — what the production image
       has (the ``real`` extra), and the exact module the indexer calls, so the
       count this API reports equals what was actually removed.
    2. The same file loaded from the sibling source tree. The offline default
       install deliberately does **not** carry the retrieval package (see
       ``app/engine/build.py``), and redaction is pure ``re`` with no
       dependencies, so loading the one implementation by path is better than
       keeping a second copy of these patterns in this service.
    3. ``services/ingestion/redact.py``, the ingestion-side implementation, as
       a last resort.

    Returns a callable ``str -> (str, int)``, or ``None``.
    """
    try:
        from retrieval.redact import redact as installed

        return installed
    except ModuleNotFoundError:
        pass

    root = _repo_root()
    candidates = (
        (root / "services" / "retrieval" / "retrieval" / "redact.py", "redact"),
        (root / "services" / "ingestion" / "redact.py", "redact_text"),
    )
    for path, attribute in candidates:
        if not path.is_file():
            continue
        try:
            module = _load_from_path(path, f"insightgpt_shared_redact_{attribute}")
            function = getattr(module, attribute, None)
            if function is not None:
                return function
        except Exception as exc:  # noqa: BLE001 — try the next candidate
            log.warning("could not load redactor from %s: %s", path, exc)
    return None


def redact(text: str) -> tuple[str, int]:
    """Redact secrets and PII, returning ``(text, count)``.

    Raises ``RuntimeError`` when no implementation can be loaded. That is
    deliberate: the alternative is embedding an unredacted document, and
    contract §6.4 makes that worse than failing the upload.
    """
    global _REDACTOR
    if _REDACTOR is None:
        _REDACTOR = _resolve_redactor()
    if _REDACTOR is None:
        raise RuntimeError(
            "No redaction implementation is available, so this file cannot be "
            "indexed: secrets and PII must be removed before anything is embedded."
        )
    result = _REDACTOR(text)
    # ``retrieval.redact`` returns a tuple; ``ingestion.redact_text`` returns a
    # dataclass. Accept either so neither module has to change for this caller.
    if isinstance(result, tuple):
        redacted, count = result
        return str(redacted), int(count)
    return str(result.text), int(result.count)


# --------------------------------------------------------------------------
# The seam into the indexing layer (owned by the retrieval service)
# --------------------------------------------------------------------------
class IndexingUnavailableError(RuntimeError):
    """The vector-indexing backend is not installed or not reachable."""


class DatasetParseError(RuntimeError):
    """The file could not be read, so there is nothing honest to index.

    Distinct from an indexing failure: the bytes arrived and were rejected on
    their own terms (corrupt, encrypted, a scanned PDF with no text layer),
    which is a permanent condition for that file rather than something a
    retry resolves.
    """


def _configured_collection() -> str:
    from retrieval.config import load_config

    return load_config().collection


def _assert_ours(collection: str) -> None:
    """Refuse to touch a collection that is not InsightGPT's (contract §3)."""
    if not collection.startswith(COLLECTION_PREFIX):
        raise RuntimeError(
            f"Refusing to modify Qdrant collection {collection!r}: this instance is "
            f"shared with another project and InsightGPT may only write to "
            f"collections named {COLLECTION_PREFIX}*. Set QDRANT_COLLECTION."
        )


def index_dataset_text(
    *,
    dataset_id: str,
    name: str,
    kind: str,
    text: str,
    created_at: str | None = None,
) -> int:
    """Chunk, embed and upsert one dataset's already-redacted text.

    Delegates to the retrieval service, which owns chunking, embedding and the
    Qdrant write. Every point it writes must carry ``dataset_id`` in its
    payload, because deletion is a filtered delete on that field.

    Returns the number of points actually written.
    """
    try:
        from retrieval.datasets import index_dataset
    except ModuleNotFoundError as exc:
        raise IndexingUnavailableError(
            "The retrieval package is not installed in this service, so uploaded "
            "files cannot be embedded. Install the 'real' extra and set "
            "RETRIEVER=qdrant."
        ) from exc

    _assert_ours(_configured_collection())
    result = index_dataset(
        dataset_id=dataset_id,
        name=name,
        kind=kind,
        text=text,
        created_at=created_at,
    )
    # The indexer reports a recoverable failure in-band rather than raising.
    # Propagate its own wording so the dataset's ``error`` says what actually
    # went wrong instead of a generic message invented here.
    error = getattr(result, "error", None)
    if error:
        raise RuntimeError(str(error))
    return int(getattr(result, "chunks", result))


def delete_dataset_points(dataset_id: str) -> int:
    """Remove every Qdrant point whose payload ``dataset_id`` matches.

    A filtered delete inside our own collection — never a collection drop, a
    recreate, or a prune (contract §3). Returns the number of points removed.
    """
    try:
        from retrieval.datasets import delete_dataset
    except ModuleNotFoundError as exc:
        raise IndexingUnavailableError(
            "The retrieval package is not installed in this service, so indexed "
            "points cannot be removed."
        ) from exc

    _assert_ours(_configured_collection())
    return int(delete_dataset(dataset_id) or 0)


# --------------------------------------------------------------------------
# Parsing — reuses the attachment processor, no second parser
# --------------------------------------------------------------------------
#: Extension -> kind. The accepted set is the contract's, exactly.
ACCEPTED: dict[str, DatasetKind] = {
    ".csv": "tabular",
    ".tsv": "tabular",
    ".xlsx": "tabular",
    ".json": "tabular",
    ".pdf": "document",
    ".txt": "document",
    ".md": "document",
    ".docx": "document",
}

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 50 * 1024 * 1024
MAX_FILES = 10


def extension_of(filename: str) -> str:
    return Path(filename.replace("\\", "/")).suffix.lower()


def parse_to_text(filename: str, payload: bytes) -> tuple[str, DatasetKind]:
    """Extract plain text from an uploaded file.

    Delegates to ``app.engine.attachments``, which already parses every accepted
    type for the ``/ask`` attachment path (CSV/TSV via the stdlib reader, XLSX
    via openpyxl, PDF via pypdf, DOCX via zipfile, JSON, and plain text). Writing
    a second parser here would mean two things to keep correct, so the bytes are
    handed over as a base64 data URL — the shape that processor already accepts
    for binary content.

    Returns ``(text, kind)``. ``kind`` refines the extension's default with what
    the parse actually found, so a JSON file that is not a record array is
    reported as a document rather than as a table that does not exist.
    """
    import base64

    from ..engine.attachments import AttachmentInput, process_attachment

    encoded = base64.b64encode(payload).decode("ascii")
    processed = process_attachment(
        AttachmentInput(
            name=safe_filename(filename),
            content=f"data:application/octet-stream;base64,{encoded}",
        )
    )
    text = processed.text_content or ""
    _reject_unparsed(text)
    kind: DatasetKind = ACCEPTED.get(extension_of(filename), "document")
    if kind == "tabular" and processed.table is None:
        kind = "document"
    return text, kind


# `process_attachment` never raises on a bad file: it returns the failure as
# prose, because for the /ask path a sentence explaining that a PDF could not
# be read is a reasonable thing to show beside an answer. On this path it is
# not - it gets redacted, chunked, embedded into Qdrant and reported as
# `ready` with a real-looking chunk count, so a corrupt upload becomes a
# searchable document whose entire content is an apology. Worse, the badge
# says the file is indexed when nothing of it is.
_UNPARSED_MARKERS = (
    "(could not parse",
    "(no extractable text",
    "(empty ",
    "(malformed json",
    "(unsupported ",
)


def _reject_unparsed(text: str) -> None:
    """Turn a parser's placeholder prose back into the failure it describes."""
    head = text.strip()[:200].lower()
    if any(marker in head for marker in _UNPARSED_MARKERS) or "parse error" in head:
        # Carry the parser's own words: it names the format and the reason.
        raise DatasetParseError(text.strip()[:200])
    if not head:
        raise DatasetParseError("No text could be extracted from this file.")


__all__ = [
    "ACCEPTED",
    "COLLECTION_PREFIX",
    "Dataset",
    "DatasetJob",
    "IndexingUnavailableError",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_REQUEST_BYTES",
    "StoredDataset",
    "data_dir",
    "delete_dataset_points",
    "extension_of",
    "get",
    "index_dataset_text",
    "job_of",
    "list_all",
    "parse_to_text",
    "put",
    "redact",
    "remove",
    "remove_files",
    "reset_state",
    "safe_filename",
    "save_upload",
    "set_data_dir",
    "update",
]
