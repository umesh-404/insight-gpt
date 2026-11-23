"""Durable registry and side-effect helpers for uploaded datasets.

The registry is a JSON file written atomically (tmp-then-rename), behind the
same shape ``app.api.sources_store`` uses for data sources: a backend chosen
from the environment, a ``durable`` property that tells the truth about whether
a restart is survivable, and a fallback to process-local memory that never
pretends otherwise. It was in-process until phase 2, and that was the worst
available combination — restarting the API emptied the dataset list in the UI
while the raw files and the Qdrant points were still there, so the product
denied the existence of data it was still holding.

Because the file and the vector store can disagree, the registry **reconciles
on load** against what Qdrant actually holds (see :meth:`DatasetRegistry.reconcile`).
Indexing runs on a background worker thread, so every read and write goes
through a lock.

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
import json
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


#: Fields whose change does not justify a disk write. ``progress`` and
#: ``message`` describe a job that is in flight: they tick several times a
#: second during an embed, they are meaningless after a restart (no worker
#: survives it), and flushing the whole registry for each one would turn a
#: progress bar into disk churn. Everything else is persisted immediately.
_EPHEMERAL_FIELDS = frozenset({"progress", "message"})

#: ``Callable[[str], int]``, kept loose so a test can pass a plain stub.
CountPoints = Any


class DatasetRegistry:
    """The dataset registry, durable across a restart.

    Two backends, mirroring :class:`app.api.sources_store.SourceStore`:

    * **file** — a JSON document written tmp-then-rename, so a crash mid-write
      cannot truncate the registry.
    * **memory** — process-local, used when no path is configured (the tests)
      and the one case where :attr:`durable` is false.

    There is deliberately no Postgres backend here, unlike ``sources``: that
    table is created by warehouse bootstrap, the dataset registry has no such
    table, and a half-tested SQL path would be a second way to lose the list.
    The file backend already satisfies the requirement, and :attr:`durable`
    reports honestly either way.
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._file_path = Path(file_path) if file_path else None
        self._lock = threading.RLock()
        self._datasets: dict[str, StoredDataset] = {}
        #: Set when the registry file could not be read, or when rows in
        #: it were skipped. An empty list and an unreadable list are very
        #: different statements to make to someone about their uploads.
        self.load_error: str | None = None
        self._backend = "file" if self._file_path is not None else "memory"
        self._reconciled = False
        if self._file_path is not None:
            self._load_file()

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def durable(self) -> bool:
        """True when the registry actually survives a restart."""
        return self._backend == "file"

    @property
    def path(self) -> Path | None:
        return self._file_path

    # -- reads -------------------------------------------------------------

    def get(self, dataset_id: str) -> StoredDataset | None:
        with self._lock:
            return self._datasets.get(dataset_id)

    def list_all(self) -> list[StoredDataset]:
        with self._lock:
            return sorted(self._datasets.values(), key=lambda d: d.created_at, reverse=True)

    # -- writes ------------------------------------------------------------

    def put(self, record: StoredDataset) -> None:
        with self._lock:
            self._datasets[record.id] = record
            self._trim_locked()
            self._flush_locked()

    def remove(self, dataset_id: str) -> StoredDataset | None:
        with self._lock:
            removed = self._datasets.pop(dataset_id, None)
            if removed is not None:
                self._flush_locked()
            return removed

    def update(self, dataset_id: str, **fields: Any) -> StoredDataset | None:
        """Patch a stored record in place under the lock. Unknown ids are
        ignored — a dataset deleted mid-index must not be resurrected by its
        own worker."""
        with self._lock:
            record = self._datasets.get(dataset_id)
            if record is None:
                return None
            updated = record.model_copy(update=fields)
            self._datasets[dataset_id] = updated
            if not set(fields).issubset(_EPHEMERAL_FIELDS):
                self._flush_locked()
            return updated

    def clear(self) -> None:
        """Drop every record (tests, and ``reset_state``)."""
        with self._lock:
            self._datasets.clear()
            self._reconciled = False
            self._flush_locked()

    def _trim_locked(self) -> None:
        if len(self._datasets) <= MAX_DATASETS:
            return
        oldest = sorted(self._datasets, key=lambda i: self._datasets[i].created_at)
        dropped = oldest[: len(self._datasets) - MAX_DATASETS]
        for dataset_id in dropped:
            self._datasets.pop(dataset_id, None)
        # The cap used to be invisible because the whole registry was lost on
        # every restart anyway. Now it is the only way a record can disappear
        # while its raw file and its Qdrant points stay — the same dishonesty
        # this store exists to remove, so at least it is never silent.
        log.warning(
            "dataset registry: at the %d-dataset cap, so the oldest record(s) "
            "were dropped from the list while their uploaded files and indexed "
            "chunks remain on disk: %s",
            MAX_DATASETS,
            ", ".join(dropped),
        )

    # -- file backend ------------------------------------------------------

    def _load_file(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — a corrupt registry is not fatal
            # Recorded, not just logged. Serving an unreadable registry as an
            # empty list tells the reader "you have uploaded nothing", which
            # is a false statement about their own data rather than a missing
            # one. The caller can surface this; a log line reaches nobody who
            # is looking at the screen.
            self.load_error = f"the dataset registry at {self._file_path} could not be read: {exc}"
            log.warning("dataset registry: could not read %s (%s).", self._file_path, exc)
            return

        # Per row, not per file. As one comprehension inside one `try`, a
        # single unparseable record discarded every dataset on the machine -
        # and the worst trigger is an upgrade, where adding one required field
        # to `StoredDataset` would empty every registry everywhere on the next
        # boot while the files and vectors sat there intact. One bad row now
        # costs one row.
        loaded: dict[str, StoredDataset] = {}
        skipped = 0
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict) or not row.get("id"):
                skipped += 1
                continue
            try:
                loaded[row["id"]] = StoredDataset.model_validate(row)
            except Exception as exc:  # noqa: BLE001 — one unreadable row
                skipped += 1
                log.warning(
                    "dataset registry: skipped unreadable record %s (%s)",
                    row.get("id"), exc,
                )
        self._datasets = loaded
        if skipped:
            self.load_error = (
                f"{skipped} record(s) in the dataset registry could not be read and "
                "were skipped; their uploaded files and indexed chunks are untouched."
            )

    def _flush_locked(self) -> None:
        if self._file_path is None:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [r.model_dump(mode="json") for r in self._datasets.values()]
            # Write-then-rename: a crash mid-write must not truncate the registry.
            tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._file_path)
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            log.warning("dataset registry: could not write %s (%s).", self._file_path, exc)

    # -- reconciliation ----------------------------------------------------

    def reconcile(self, count_points: CountPoints | None = None) -> list[str]:
        """Make the loaded registry agree with what the vector store holds.

        The file and Qdrant are two separate pieces of state and they can
        disagree: points were deleted out of band, the collection was
        recreated, or — the common one — a restart killed an indexing job
        mid-flight. Two of those disagreements are the exact dishonesty
        contract §3.6 forbids, so each is resolved rather than carried:

        ========================  ================  =======================================
        status on disk            points in Qdrant  becomes
        ========================  ================  =======================================
        ``ready``                 > 0               ``ready``, ``chunks`` = the live count
        ``ready``                 0                 ``failed`` — the index is empty, so the
                                                    green badge was a lie
        ``indexing`` / ``pending``> 0               ``ready`` — the write finished, only the
                                                    bookkeeping was lost
        ``indexing`` / ``pending``0                 ``failed`` — interrupted, and nothing is
                                                    coming to finish it
        ``indexing`` / ``pending``unknown           ``failed``, and the reason says the index
                                                    could not be checked
        ``failed``                > 0               ``failed`` (unchanged), but ``chunks`` is
                                                    set so ``DELETE`` cleans the orphans up
        ``failed``                0                 ``failed``, untouched
        ========================  ================  =======================================

        ``chunks`` always becomes the number Qdrant reports, never the number
        the last process believed it wrote.

        If the count cannot be read at all (the retrieval package is not
        installed, or Qdrant is down) nothing is *inferred* from a zero that
        might only mean "could not ask": a ``ready`` dataset is left alone,
        because an unreachable vector store is not evidence that its points are
        gone. A dataset stuck at ``indexing``/``pending`` is still resolved to
        ``failed``, because no worker survived the restart either way — leaving
        it mid-flight for ever is the one state §3.6 rules out outright.

        Returns the ids whose status changed, and is idempotent.
        """
        counter = count_points or _qdrant_chunk_count
        with self._lock:
            records = list(self._datasets.values())
        changed: list[str] = []
        counts_available = True

        for record in records:
            count: int | None = None
            if counts_available:
                count = _safe_count(counter, record.id)
                if count is None:
                    # Stop asking. If the store cannot be reached for one
                    # dataset it cannot be reached for the next fifty either,
                    # and a per-dataset timeout would make startup crawl.
                    counts_available = False

            fields = _reconciled_fields(record, count)
            if fields:
                self.update(record.id, **fields)
                changed.append(record.id)

        with self._lock:
            self._reconciled = True
        if changed:
            log.warning(
                "dataset registry: reconciled %d dataset(s) against the vector "
                "store on load: %s",
                len(changed),
                ", ".join(changed),
            )
        return changed

    def reconcile_once(self, count_points: CountPoints | None = None) -> None:
        with self._lock:
            if self._reconciled:
                return
        self.reconcile(count_points)


def _qdrant_chunk_count(dataset_id: str) -> int:
    """Points currently indexed for one dataset, read from Qdrant.

    Lazily imported for the same reason as the rest of the seam: the offline
    API image carries no retrieval package.
    """
    from retrieval.datasets import dataset_chunk_count

    return int(dataset_chunk_count(dataset_id) or 0)


def _safe_count(counter: CountPoints, dataset_id: str) -> int | None:
    """The live point count, or ``None`` when it could not be read.

    ``None`` means "unknown", and is never treated as zero: that distinction is
    the whole difference between "your data is gone" and "I could not ask".
    """
    try:
        return int(counter(dataset_id) or 0)
    # ``SystemExit`` deliberately: the retrieval package raises it for a
    # missing config file, and a reconciliation pass must not take the
    # process down on the way up.
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        log.warning(
            "dataset registry: could not read the indexed chunk count for %s "
            "(%s) — leaving indexed datasets as they are.",
            dataset_id,
            f"{type(exc).__name__}: {exc}"[:200],
        )
        return None


_INTERRUPTED = (
    "Indexing was interrupted when the service restarted, and nothing of this "
    "file reached the index. The uploaded file was kept, so it can be removed "
    "and uploaded again."
)
# The same interruption, but the vector store could not be asked. Saying
# "nothing reached the index" here would be a guess dressed as a finding, and
# the guess is the thing this project refuses.
_INTERRUPTED_UNVERIFIED = (
    "Indexing was interrupted when the service restarted. The vector store "
    "could not be reached to check what, if anything, was indexed, so this "
    "file is not usable as it stands. The upload was kept; delete it and "
    "upload it again once the vector store is back."
)
_VANISHED = (
    "This dataset was recorded as indexed, but the vector store now holds no "
    "chunks for it, so nothing about it is searchable. The uploaded file was "
    "kept, so it can be removed and uploaded again."
)


def _reconciled_fields(record: StoredDataset, count: int | None) -> dict[str, Any]:
    """The patch that makes one record honest about the index. Empty if none."""
    if record.status in ("pending", "indexing"):
        if count:
            return {
                "status": "ready",
                "chunks": count,
                "progress": 1.0,
                "error": None,
                "message": f"Indexed {count} chunk(s) (confirmed after a restart)",
            }
        if count == 0:
            return {
                "status": "failed",
                "chunks": 0,
                "progress": 1.0,
                "error": _INTERRUPTED,
                "message": "Indexing was interrupted",
            }
        # count is None: interrupted, and unverifiable. `chunks` is left at
        # whatever was recorded rather than asserted to be zero.
        return {
            "status": "failed",
            "progress": 1.0,
            "error": _INTERRUPTED_UNVERIFIED,
            "message": "Indexing was interrupted",
        }
    if count is None:
        return {}  # unknown is not zero; see _safe_count
    if record.status == "ready" and count == 0:
        return {
            "status": "failed",
            "chunks": 0,
            "progress": 1.0,
            "error": _VANISHED,
            "message": "No indexed chunks remain",
        }
    if record.chunks != count:
        # Includes a `failed` record with orphaned points: the status stands,
        # but `chunks` has to be truthful or DELETE will skip the vector
        # cleanup (the route only calls the filtered delete when chunks > 0)
        # and leave them searchable for ever.
        return {"chunks": count}
    return {}


# --------------------------------------------------------------------------
# The process-wide registry
# --------------------------------------------------------------------------
_REGISTRY: DatasetRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def _default_registry_path() -> Path:
    """``data/datasets.json`` at the repo root, beside ``data/sources.json``.

    Anchored at the repo rather than the process cwd for the same reason
    ``sources_store`` is: the API is normally launched from ``services/api``, so
    a bare relative path would write the registry into ``services/api/data/`` —
    durable, but somewhere nobody looks and nothing ignores.
    """
    return _repo_root() / "data" / "datasets.json"


def _under_test() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ


def registry() -> DatasetRegistry:
    """The registry this deployment is configured for, built once.

    Durable by default: a plain ``uvicorn`` with no extra environment keeps its
    dataset list across a restart, because forgetting it while the files and
    vectors remain is the bug this store exists to fix. Under pytest the
    default is memory, so a test run can neither write to nor inherit from the
    developer's real registry; ``DATASETS_STORE_PATH`` still wins in both cases.

    Reconciliation runs once, on first use, so an import never opens a socket
    and a test suite never needs a vector store.
    """
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            configured = os.getenv("DATASETS_STORE_PATH")
            path = configured or (None if _under_test() else _default_registry_path())
            _REGISTRY = DatasetRegistry(path)
            log.info(
                "dataset registry: %s backend%s",
                _REGISTRY.backend,
                f" at {_REGISTRY.path}" if _REGISTRY.path else " (not durable)",
            )
        current = _REGISTRY
    current.reconcile_once()
    return current


def set_registry(store_or_path: DatasetRegistry | str | Path | None) -> DatasetRegistry:
    """Point the module at another registry — a restart, in a test.

    Passing ``None`` drops back to a fresh memory registry.
    """
    global _REGISTRY
    with _REGISTRY_LOCK:
        if isinstance(store_or_path, DatasetRegistry):
            _REGISTRY = store_or_path
        else:
            _REGISTRY = DatasetRegistry(store_or_path)
        return _REGISTRY


def reset_state() -> None:
    """Clear the registry (tests). Does not touch raw uploads on disk."""
    registry().clear()


def put(record: StoredDataset) -> None:
    registry().put(record)


def get(dataset_id: str) -> StoredDataset | None:
    return registry().get(dataset_id)


def list_all() -> list[StoredDataset]:
    return registry().list_all()


def remove(dataset_id: str) -> StoredDataset | None:
    return registry().remove(dataset_id)


def update(dataset_id: str, **fields: Any) -> StoredDataset | None:
    return registry().update(dataset_id, **fields)


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
    progress: Any = None,
) -> int:
    """Chunk, embed and upsert one dataset's already-redacted text.

    Delegates to the retrieval service, which owns chunking, embedding and the
    Qdrant write. Every point it writes must carry ``dataset_id`` in its
    payload, because deletion is a filtered delete on that field.

    ``progress``, if given, is called as ``progress(fraction, message)`` by the
    indexer as real work completes — per embedding batch during the embed,
    which is the slow step. It is passed straight through; this module adds no
    motion of its own, because a bar that moves while nothing happens is worse
    than one that sits still (contract §3.8).

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
        progress=progress,
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
    "DatasetParseError",
    "DatasetRegistry",
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
    "registry",
    "remove",
    "remove_files",
    "reset_state",
    "safe_filename",
    "save_upload",
    "set_data_dir",
    "set_registry",
    "update",
]
