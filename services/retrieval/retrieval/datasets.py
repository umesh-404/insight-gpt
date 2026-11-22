"""The indexing seam for uploaded datasets.

This module is the whole public surface the datasets API needs. It exists so
that the upload router owns HTTP, validation and bookkeeping, and knows nothing
about Qdrant, embeddings or chunk sizes::

    from retrieval.datasets import index_dataset, delete_dataset

    result = index_dataset("ds_a1b2c3d4e5f6", "q3_sales.csv", Path(tmp_file))
    dataset.chunks = result.chunks
    dataset.redactions = result.redactions
    dataset.status = "ready" if result.ok else "failed"
    dataset.error = result.error

    removed = delete_dataset("ds_a1b2c3d4e5f6")   # points deleted

Three properties are deliberate:

**It reuses the one pipeline.** Redaction -> chunking -> embedding -> storage is
:class:`retrieval.indexer.Indexer`, the same code the curated corpus goes
through. A second pipeline would be a second place for redaction to be
forgotten, and the premise of the product is that secrets never reach the
vector store (contract §6.4).

**Deletion is a filtered delete.** Every point written here carries
``dataset_id`` in its payload, so forgetting one dataset removes exactly its
points. Nothing in this package drops a collection, and
:func:`retrieval.store.assert_insightgpt_collection` refuses to delete from a
collection outside the ``insightgpt_`` namespace — the Qdrant instance may be
shared with other projects.

**A failure is returned, not raised.** Indexing runs in a background job, and
the thing that job must never do is leave a dataset at ``"indexing"`` forever
(contract §6.6). So operational failures — Ollama down, a corrupt PDF, a file
with no extractable text — come back as a result with ``ok is False`` and a
human-readable ``error``. Only programmer errors (no ``dataset_id``, both text
and path) raise.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .config import RetrievalConfig, get_config
from .embedder import make_embedder
from .indexer import Indexer
from .loaders import (
    SUPPORTED_EXTENSIONS,
    DatasetExtractionError,
    UnsupportedDatasetFile,
    classify,
    extract_text,
)
from .models import Document
from .store import Store

log = logging.getLogger("retrieval.datasets")

# The payload ``source_type`` every uploaded dataset chunk carries. Distinct
# from ticket/review/report/email on purpose: an answer drawn from a user's
# upload must be distinguishable from one drawn from the curated retail corpus,
# never silently blended with it (contract §6.1).
DATASET_SOURCE_TYPE = "dataset"

# Progress is reported at stage boundaries, not per chunk. See `_Progress`.
ProgressCallback = Callable[[float, str], None]

__all__ = [
    "DATASET_SOURCE_TYPE",
    "SUPPORTED_EXTENSIONS",
    "IndexResult",
    "classify",
    "dataset_chunk_count",
    "delete_dataset",
    "index_dataset",
]


@dataclass
class IndexResult:
    """What one ``index_dataset`` call actually did.

    ``chunks`` is read back from Qdrant after the write rather than taken from
    what the writer believed it wrote, because it is the number the UI shows
    and the number a demo gets judged on.
    """

    dataset_id: str
    name: str
    kind: str                       # "document" | "tabular"
    chunks: int = 0
    redactions: int = 0
    characters: int = 0             # extracted text length, for sanity checks
    collection: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the dataset is genuinely searchable.

        Zero chunks is NOT success. An "indexed" dataset that returns nothing
        looks like a working upload and is the exact dishonesty contract §6.3
        forbids, so the caller must be able to mark it failed.
        """
        return self.error is None and self.chunks > 0


def index_dataset(
    dataset_id: str,
    name: str,
    text_or_path: str | Path | None = None,
    *,
    text: str | None = None,
    path: str | Path | None = None,
    kind: str | None = None,
    created_at: str | None = None,
    config: RetrievalConfig | None = None,
    progress: ProgressCallback | None = None,
) -> IndexResult:
    """Make one uploaded dataset searchable. Returns an :class:`IndexResult`.

    The content is given once, in any of three equivalent ways:

    * ``index_dataset(id, name, Path("/tmp/upload.csv"))`` — a
      :class:`~pathlib.Path` third argument is a FILE, read and extracted here;
    * ``index_dataset(id, name, "some already-extracted text")`` — a ``str``
      third argument is TEXT, used as-is;
    * ``index_dataset(id, name, path=...)`` or ``text=...`` — explicit, and the
      only way to pass a file by its path as a string.

    The ``Path`` vs ``str`` rule in the positional form is the one piece of
    magic here, and it is resolved by TYPE, never by guessing whether a string
    happens to name an existing file — a one-line CSV pasted as text must not
    become a file-not-found, and a filename must not get embedded as its own
    content.

    ``kind`` ("document" or "tabular") overrides what this module infers from
    the filename, for a caller that has already classified the upload and whose
    answer must be the one the user is shown. ``created_at`` likewise overrides
    the indexed date with the upload's own timestamp, so a date filter on a
    question ranges over when the user uploaded the file rather than when the
    indexer happened to run.

    ``progress``, if given, is called as ``progress(fraction, message)`` at
    stage boundaries (read, extracted, embedding, written). It is coarse by
    design: the expensive step is a single batched embed call per 32 chunks and
    splitting it to report a smoother number would make indexing slower to look
    busier.
    """
    if not dataset_id:
        raise ValueError("index_dataset requires a dataset_id")
    if not str(dataset_id).startswith("ds_"):
        # Not fatal, but it means the caller's id scheme and the API contract
        # have drifted, and the payload field is what deletion keys on.
        log.warning("dataset_id %r does not look like 'ds_<hex12>'", dataset_id)

    source_text, source_path = _resolve_source(text_or_path, text=text, path=path)
    cfg = config or get_config()
    report = _Progress(progress)
    result = IndexResult(
        dataset_id=dataset_id,
        name=name,
        kind="document",
        collection=cfg.collection,
    )

    # ---- 1. get text ------------------------------------------------------
    report(0.05, "reading the file")
    if source_path is not None:
        try:
            source_text, result.kind, notes = extract_text(source_path, name=name)
        except UnsupportedDatasetFile as exc:
            # A permanent error: the caller must not offer "try again" (§6.7).
            result.error = str(exc)
            return result
        except DatasetExtractionError as exc:
            result.error = str(exc)
            return result
        result.notes.extend(notes)
    else:
        try:
            result.kind = classify(name)
        except UnsupportedDatasetFile:
            result.kind = "document"  # inline text has no extension to judge
    if kind:
        # The caller's classification wins: it is the one the user was shown.
        result.kind = kind

    source_text = (source_text or "").strip()
    result.characters = len(source_text)
    if not source_text:
        result.error = (
            f"no extractable text in {name}. A scanned PDF or an empty file "
            f"cannot be indexed; this build does not run OCR."
        )
        return result
    report(0.2, f"extracted {len(source_text):,} characters")

    # ---- 2. index through the one pipeline --------------------------------
    doc = Document(
        doc_id=dataset_id,          # one dataset == one document, so a re-index
                                    # overwrites its own chunks (see retrieval.ids)
        source_type=DATASET_SOURCE_TYPE,
        # The dataset's name IS the citation title, which is how an answer says
        # which upload it came from (contract §6.2).
        title=name,
        body=source_text,
        date=created_at or datetime.now(UTC).date().isoformat(),
        dataset_id=dataset_id,
        dataset_name=name,
    )

    try:
        store = Store(cfg)
        store.ensure_collection()
        report(0.3, "embedding")
        with make_embedder(cfg.embedding) as embedder:
            # Checked before chunking so a missing model fails in the first
            # second instead of after every chunk has been prepared.
            embedder.health()
            stats = Indexer(cfg, store, embedder).index_documents([doc])
        report(0.9, "written to the vector store")
    except Exception as exc:  # noqa: BLE001 - the job must end, not hang (§6.6)
        log.exception("indexing dataset %s failed", dataset_id)
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.redactions = stats.redactions
    if stats.errors:
        result.error = "; ".join(stats.errors)
        return result

    result.chunks = store.count_dataset(dataset_id)
    if result.chunks == 0:
        result.error = (
            f"{name} produced no indexable chunks, so nothing about it is "
            f"searchable."
        )
        return result
    report(1.0, f"{result.chunks} chunks indexed")
    return result


def delete_dataset(dataset_id: str, *, config: RetrievalConfig | None = None) -> int:
    """Forget one dataset. Returns the number of points removed.

    A filtered delete on the ``dataset_id`` payload field. The collection also
    holds the curated corpus and every other dataset, so it is never dropped;
    :func:`retrieval.store.assert_insightgpt_collection` additionally refuses
    any delete outside the ``insightgpt_`` namespace.
    """
    if not dataset_id:
        raise ValueError("delete_dataset requires a dataset_id")
    return Store(config or get_config()).delete_dataset(dataset_id)


def dataset_chunk_count(dataset_id: str, *, config: RetrievalConfig | None = None) -> int:
    """Points currently indexed for one dataset, read from Qdrant."""
    return Store(config or get_config()).count_dataset(dataset_id)


# ---------------------------------------------------------------------- helpers
def _resolve_source(
    text_or_path: str | Path | None,
    *,
    text: str | None,
    path: str | Path | None,
) -> tuple[str | None, Path | None]:
    """Normalize the three calling styles into ``(text, path)``, exactly one set."""
    given = [x for x in (text_or_path, text, path) if x is not None]
    if not given:
        raise ValueError("index_dataset needs the dataset's content: pass text or path")
    if len(given) > 1:
        raise ValueError("index_dataset takes the content once: text OR path, not both")

    if path is not None:
        return None, Path(path)
    if text is not None:
        return text, None
    # Positional: a Path is a file, a str is text. By type, not by guessing.
    if isinstance(text_or_path, Path):
        return None, text_or_path
    return str(text_or_path), None


class _Progress:
    """Call the caller's progress callback without letting it break indexing.

    A UI callback that raises must not fail an otherwise-good index, and it must
    not be the reason a dataset is marked failed.
    """

    def __init__(self, callback: ProgressCallback | None) -> None:
        self._callback = callback

    def __call__(self, fraction: float, message: str) -> None:
        if self._callback is None:
            return
        try:
            self._callback(fraction, message)
        except Exception:  # noqa: BLE001
            log.warning("progress callback raised; continuing", exc_info=True)
