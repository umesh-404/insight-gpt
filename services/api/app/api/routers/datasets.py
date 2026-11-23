"""Uploaded datasets (contract §4) — upload, list, read, poll, delete.

The upload path, per file and in this order:

1. **Validate** the extension and the size *before* anything is read into
   memory in full or written to disk, so an unsupported type is a 400 naming
   what is accepted and an oversized file is a 413 rather than a hang.
2. **Persist** the raw bytes under ``data/ingested/<dataset_id>/`` so a
   re-index never needs a re-upload.
3. **Parse** to text by reusing ``app.engine.attachments`` — the processor the
   ``/ask`` attachment path already uses for every one of these types.
4. **Redact** secrets and PII, and record the count. This happens before the
   text reaches the indexer, never after (contract §6.4).
5. **Chunk, embed and upsert** through the retrieval service, which stamps
   ``dataset_id`` onto every point it writes.

Steps 2-5 run on a background worker; the request returns ``202`` with a
``DatasetJob`` the client polls at ``GET /datasets/{id}/status``. Unlike
``/reports``, which generates inline because the offline stack answers in
seconds, embedding a 25 MB PDF through a local model is minutes of work: far
past any browser or proxy timeout, which is why the contract specifies a job
and a poll endpoint at all. The worker is a plain synchronous function handed
to Starlette's ``BackgroundTasks``, which runs it in the threadpool — so the
event loop keeps serving the polls that make the job visible.

A worker that raises sets ``status="failed"`` with the reason on the dataset. A
dataset is never left sitting at ``"indexing"`` because something broke
(contract §6.6).

Honesty note: an uploaded file is **retrieval** material and nothing more. It
does not become a governed metric, and nothing here touches the semantic layer
or the warehouse.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from pydantic import BaseModel

from ...auth.roles import Role, require_role
from ...engine.build import datasets_are_searchable
from .. import datasets_store as store
from ..datasets_store import Dataset, DatasetJob, StoredDataset
from ..deps import rate_limit
from ..errors import APIError, DependencyUnavailableError, NotFoundError
from ..observability import dataset_event, file_fingerprint

log = logging.getLogger("insightgpt.datasets")

router = APIRouter(tags=["datasets"])

#: Read in 1 MiB slices so the per-file cap can be enforced during the read
#: rather than after it — the point of a 413 is that the bytes are refused.
_CHUNK = 1024 * 1024


class UnsupportedFileTypeError(APIError):
    """A permanent rejection: this file type will never be indexable.

    Separate from a generic 400 so the client can tell a retryable failure from
    one where offering "try again" would be a lie (contract §6.7).
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "unsupported_file_type"


class PayloadTooLargeError(APIError):
    # Spelled numerically: Starlette renamed this constant and deprecated the
    # old name, so neither spelling is portable across the versions we support.
    status_code = 413
    code = "payload_too_large"


class DeleteResult(BaseModel):
    deleted: bool


def _accepted() -> list[str]:
    return sorted(store.ACCEPTED)


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.0f} MB"


async def _read_capped(upload: UploadFile, *, budget: int) -> bytes:
    """Read one upload, refusing it as soon as it exceeds the smaller of the
    per-file cap and what is left of the per-request budget."""
    limit = min(store.MAX_FILE_BYTES, budget)
    buffer = bytearray()
    while True:
        piece = await upload.read(_CHUNK)
        if not piece:
            break
        buffer.extend(piece)
        if len(buffer) > limit:
            if limit == store.MAX_FILE_BYTES:
                raise PayloadTooLargeError(
                    f"{upload.filename or 'file'} is larger than the "
                    f"{_mb(store.MAX_FILE_BYTES)} per-file limit.",
                    details={
                        "filename": upload.filename,
                        "max_file_bytes": store.MAX_FILE_BYTES,
                        "retryable": False,
                    },
                )
            raise PayloadTooLargeError(
                f"This upload exceeds the {_mb(store.MAX_REQUEST_BYTES)} total "
                f"request limit.",
                details={
                    "max_request_bytes": store.MAX_REQUEST_BYTES,
                    "retryable": False,
                },
            )
    return bytes(buffer)


def _validate_type(filename: str | None) -> None:
    extension = store.extension_of(filename or "")
    if extension in store.ACCEPTED:
        return
    name = filename or "(unnamed)"
    detail = f"{name}: {extension or 'no extension'} is not an accepted file type."
    raise UnsupportedFileTypeError(
        f"{detail} Accepted types are {', '.join(_accepted())}.",
        details={
            "filename": filename,
            "extension": extension or None,
            "accepted": _accepted(),
            # Permanent: re-sending the same file cannot succeed.
            "retryable": False,
        },
    )


#: The slice of the bar the indexer owns. Everything before it — reading the
#: persisted file, parsing, redacting — is seconds; the embed is minutes.
_INDEX_FROM = 0.45
_INDEX_TO = 0.95


def _progress_reporter(dataset_id: str):
    """A ``progress(fraction, message)`` callback for the indexing seam.

    Two things it guarantees, both of which matter more than smoothness:

    * **It never moves backwards.** The indexer reports on its own 0..1 scale
      and starts near zero ("reading the file"), while this worker has already
      parsed and redacted; a bar that jumps back to 5% reads as a restart that
      did not happen. The fraction is mapped into the indexer's slice of the
      bar and then clamped to the highest value reported so far.
    * **It carries the indexer's own words.** The message says "embedded 128 of
      420 chunks" because that is what the indexer actually just did. Nothing
      here invents a stage name or a number (contract §3.8).
    """
    highest = _INDEX_FROM

    def report(fraction: float, message: str) -> None:
        nonlocal highest
        mapped = _INDEX_FROM + (_INDEX_TO - _INDEX_FROM) * max(0.0, min(1.0, fraction))
        highest = max(highest, mapped)
        # Progress is deliberately not persisted (see `datasets_store`): it
        # describes a job in flight, and no job survives a restart.
        store.update(dataset_id, progress=round(highest, 3), message=message)

    return report


def _index_dataset(dataset_id: str) -> None:
    """Parse -> redact -> embed one already-persisted dataset.

    Synchronous on purpose: handed to ``BackgroundTasks``, which runs it in the
    threadpool. Every exit path writes a terminal status.
    """
    record = store.get(dataset_id)
    if record is None or record.path is None:
        return  # deleted before the worker started; nothing to do
    try:
        store.update(dataset_id, status="indexing", progress=0.1, message="Reading file")
        payload = Path(record.path).read_bytes()

        text, kind = store.parse_to_text(record.name, payload)
        store.update(dataset_id, kind=kind, progress=0.3, message="Redacting")

        redacted, redactions = store.redact(text)
        store.update(
            dataset_id,
            redactions=redactions,
            progress=0.45,
            message=(
                f"Redacted {redactions} secret/PII value(s); embedding"
                if redactions
                else "Embedding"
            ),
        )

        chunks = store.index_dataset_text(
            dataset_id=dataset_id,
            name=record.name,
            kind=kind,
            text=redacted,
            created_at=record.created_at.date().isoformat(),
            progress=_progress_reporter(dataset_id),
        )
        if chunks == 0:
            # "Ready" with nothing in the index would read as success in the UI
            # while the file contributes nothing to an answer — a scanned PDF
            # with no extractable text lands here. Say so instead (§6.3).
            store.update(
                dataset_id,
                status="failed",
                chunks=0,
                progress=1.0,
                error="No indexable text could be extracted from this file.",
                message="Nothing to index",
            )
            return
        store.update(
            dataset_id,
            status="ready",
            chunks=chunks,
            progress=1.0,
            message=f"Indexed {chunks} chunk(s)",
        )
    # ``SystemExit`` is listed deliberately. It is not an ``Exception``, and the
    # retrieval package raises it for a missing config file — on a worker thread
    # that would unwind silently and leave the dataset at "indexing" forever,
    # which is the one state contract §6.6 forbids. A misconfiguration is a
    # failed dataset with a reason, not a vanished one.
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — a recorded state
        reason = f"{type(exc).__name__}: {exc}"[:500]
        log.warning("dataset %s failed to index: %s", dataset_id, reason)
        store.update(
            dataset_id,
            status="failed",
            progress=1.0,
            error=reason,
            message="Indexing failed",
        )


def _require(dataset_id: str) -> StoredDataset:
    record = store.get(dataset_id)
    if record is None:
        raise NotFoundError(f"No dataset with id {dataset_id!r}.")
    return record


@router.post(
    "/datasets",
    response_model=DatasetJob,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("mutate"))],
)
async def upload_datasets(
    background: BackgroundTasks,
    files: list[UploadFile] = File(default=[], description="1-10 files to index"),
    bracketed: list[UploadFile] = File(default=[], alias="files[]"),
    _: object = Depends(require_role(Role.analyst)),
) -> DatasetJob:
    # Both spellings of the repeated field are accepted: browsers and `fetch`
    # send ``files``, while several HTTP clients append ``[]`` to a repeated
    # name. A mismatch there would be a 422 that looks like a server bug.
    files = [*files, *bracketed]
    # Refuse before taking the bytes, rather than accepting a file this
    # deployment can never read back. With the fixture retriever configured,
    # an upload would be parsed, redacted, embedded, badged "ready" with a
    # real chunk count - and then never consulted by a single question,
    # because the retriever that answers them cannot see the collection. A
    # green badge over an unreachable index is the most expensive kind of
    # lie: it costs the user their trust at the exact moment they check.
    if not datasets_are_searchable():
        raise DependencyUnavailableError(
            "This deployment cannot search uploaded files, so accepting one "
            "would be misleading: RETRIEVER is not set to 'qdrant', and the "
            "configured retriever cannot read the uploaded-document index.",
            details={"retryable": False},
        )
    if not files:
        raise UnsupportedFileTypeError(
            "No file was uploaded.",
            details={"accepted": _accepted(), "retryable": False},
        )
    if len(files) > store.MAX_FILES:
        raise PayloadTooLargeError(
            f"At most {store.MAX_FILES} files per upload; {len(files)} were sent.",
            details={"max_files": store.MAX_FILES, "retryable": False},
        )

    # Validate every type before a single byte is persisted, so a batch with one
    # bad file does not leave half of it indexed.
    for upload in files:
        _validate_type(upload.filename)

    created: list[StoredDataset] = []
    budget = store.MAX_REQUEST_BYTES
    try:
        for upload in files:
            payload = await _read_capped(upload, budget=budget)
            budget -= len(payload)
            dataset_id = f"ds_{uuid.uuid4().hex[:12]}"
            name = upload.filename or "upload"
            path = store.save_upload(dataset_id, name, payload)
            record = StoredDataset(
                id=dataset_id,
                name=name,
                kind=store.ACCEPTED[store.extension_of(name)],
                status="pending",
                bytes=len(payload),
                chunks=0,
                redactions=0,
                created_at=datetime.now(UTC),
                progress=0.0,
                message="Queued for indexing",
                path=str(path),
            )
            store.put(record)
            # Structured, and deliberately fingerprinted rather than named:
            # a filename can itself be the PII ("resignation_priya_sharma.pdf"),
            # and these logs sit on the far side of the redaction boundary.
            dataset_event(
                "dataset_upload",
                dataset_id=dataset_id,
                bytes=len(payload),
                kind=record.kind,
                **file_fingerprint(name),
            )
            created.append(record)
    except Exception:
        # The batch is rejected as a whole, so a file that was already written
        # is removed rather than left registered as "pending" with nothing ever
        # coming to index it. Nothing has been queued yet at this point.
        for record in created:
            store.remove_files(record.id)
            store.remove(record.id)
        raise

    for record in created:
        background.add_task(_index_dataset, record.id)

    lead = created[0]
    ids = ", ".join(r.id for r in created)
    return DatasetJob(
        dataset_id=lead.id,
        status="pending",
        progress=0.0,
        message=(
            f"Queued for indexing: {ids}"
            if len(created) > 1
            else f"Queued for indexing: {lead.name}"
        ),
    )


@router.get("/datasets", response_model=list[Dataset])
async def list_datasets(
    _: object = Depends(require_role(Role.viewer)),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[StoredDataset]:
    return store.list_all()[:limit]


@router.get("/datasets/{dataset_id}", response_model=Dataset)
async def get_dataset(
    dataset_id: str,
    _: object = Depends(require_role(Role.viewer)),
) -> StoredDataset:
    return _require(dataset_id)


@router.get("/datasets/{dataset_id}/status", response_model=DatasetJob)
async def get_dataset_status(
    dataset_id: str,
    _: object = Depends(require_role(Role.viewer)),
) -> DatasetJob:
    return store.job_of(_require(dataset_id))


@router.delete(
    "/datasets/{dataset_id}",
    response_model=DeleteResult,
    dependencies=[Depends(rate_limit("mutate"))],
)
async def delete_dataset(
    dataset_id: str,
    _: object = Depends(require_role(Role.analyst)),
) -> DeleteResult:
    """Remove the dataset: its Qdrant points, its stored files, its record.

    The vector removal is a **filtered delete** on the ``dataset_id`` payload
    field inside InsightGPT's own collection. It is never a collection drop —
    this Qdrant instance is shared with another project whose data must survive
    (contract §3).

    If the dataset has indexed chunks and they cannot be removed, that is a 503
    and the record stays. Reporting ``deleted: true`` while the vectors are
    still searchable would be a lie.
    """
    record = _require(dataset_id)

    if record.chunks:
        try:
            removed = store.delete_dataset_points(dataset_id)
        # ``SystemExit`` for the same reason as in the indexing worker above: a
        # misconfigured retrieval package must not take the request down.
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — becomes a 503
            raise DependencyUnavailableError(
                "The dataset's indexed chunks could not be removed, so nothing was "
                "deleted. Try again once the vector store is reachable.",
                details={"dataset_id": dataset_id, "reason": str(exc)[:200]},
            ) from exc
        dataset_event("dataset_deleted", dataset_id=dataset_id, points_removed=removed)

    store.remove_files(dataset_id)
    # A tabular upload also has a table in its own DuckDB database. Forgetting
    # the dataset has to forget that too, or the file is gone from every
    # surface while its rows sit on disk indefinitely. Suppressed because this
    # is cleanup, not correctness: the record is already removed, so the
    # engine reports the file as unknown and abstains either way.
    with contextlib.suppress(Exception):
        from ...warehouse.datasets_db import get_datasets_db

        get_datasets_db().drop(dataset_id)
    store.remove(dataset_id)
    return DeleteResult(deleted=True)
