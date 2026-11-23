"""Qdrant writes and collection setup: dense + sparse, delete-then-write.

The single ``insightgpt_documents`` collection holds every source type;
``source_type`` is a payload field, not a separate collection, so one query can
span types or filter to one (docs/04-retrieval-rag.md §7).

**Blast-radius rule.** Qdrant is routinely a single shared instance on a
developer machine, holding collections that belong to other projects. So this
module never drops or re-creates a collection — there is no such call anywhere
in this package, and ``tests/test_collection_safety.py`` greps the source to
keep it that way — and every write and delete path first runs
:func:`assert_insightgpt_collection`, which refuses any collection name outside
the ``insightgpt_`` namespace. Deleting a dataset is therefore a *filtered*
delete of the points carrying that ``dataset_id``, never a drop. The check is
in the code rather than in a convention, because a convention cannot fail a
test.

Two ideas from rememory carry the correctness here:

* **Deterministic ids** (``ids.point_id``): a point id is a pure function of
  ``(doc_id, chunk_index)``, so re-indexing a document overwrites its own chunks
  instead of duplicating them.
* **Delete-then-write** per document: a document that SHRANK (a ticket edited
  down, a report section removed) would otherwise leave stale tail chunks that
  nothing overwrites, and searches would return deleted text forever. So every
  chunk for a ``doc_id`` is deleted before the current chunks are upserted.
"""

from __future__ import annotations

from datetime import datetime, timezone

try:
    from datetime import UTC  # Python 3.11+
except ImportError:
    UTC = timezone.utc  # type: ignore[assignment]  # Python 3.10 fallback
from typing import Any

from qdrant_client import QdrantClient, models

from .config import RetrievalConfig
from .ids import point_id
from .models import Chunk, Document

# Payload fields indexed for filtering / diversity (docs/04 §1.1). Unindexed
# fields are still stored and returned — an index is only needed to FILTER.
# ``dataset_id`` is here because deleting an uploaded dataset is a filtered
# delete on it; without the index that filter would be a full scan.
_KEYWORD_INDEXES = (
    "source_type",
    "region",
    "category",
    "product_ref",
    "order_ref",
    "author_role",
    "dataset_id",
)

# Every collection this service is allowed to write to — and, crucially, to
# delete from — lives under this prefix.
COLLECTION_PREFIX = "insightgpt_"


class CollectionSafetyError(RuntimeError):
    """Raised when a delete would target a collection outside our namespace."""


def assert_insightgpt_collection(name: str) -> None:
    """Refuse to MUTATE a collection that is not ours.

    Called before every delete and before every write. A shared Qdrant
    instance may hold another project's collections; a mistyped or
    mis-configured collection name must stop the call rather than reach the
    wire. Deletion is the loud failure, but it is not the only one: a
    ``QDRANT_COLLECTION`` pointing at a foreign collection of the same vector
    width would otherwise have this service quietly adding payload indexes and
    upserting its own points into someone else's data.

    Reads are deliberately NOT guarded — counting points in a collection is
    harmless, and a diagnostic that cannot look is a worse diagnostic.
    """
    if not str(name).startswith(COLLECTION_PREFIX):
        raise CollectionSafetyError(
            f"refusing to write to or delete from collection {name!r}: InsightGPT only owns "
            f"collections named {COLLECTION_PREFIX}*. This Qdrant instance may "
            f"be shared with other projects, whose data must not be touched. "
            f"Check QDRANT_COLLECTION / config/retrieval.yaml."
        )


class Store:
    def __init__(self, cfg: RetrievalConfig) -> None:
        self.cfg = cfg
        self.collection = cfg.collection
        self.client = QdrantClient(url=cfg.qdrant_url, timeout=120)

    # -------------------------------------------------------------- setup
    def ensure_collection(self) -> bool:
        """Create the configured collection idempotently. Returns True if created.

        Dense vector ``dense`` (Ollama embedding) + sparse vector ``lexical``
        (term frequency, ``modifier: idf`` so Qdrant applies inverse-document-
        frequency at query time). Vector config is fixed at creation, so the
        sparse slot is declared now even before it is populated.
        """
        emb = self.cfg.embedding
        # Before anything, including the payload indexes below: a foreign
        # collection that happened to share our vector width would otherwise
        # be silently adopted and written into.
        assert_insightgpt_collection(self.collection)
        if self.client.collection_exists(self.collection):
            info = self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            size = (vectors["dense"] if isinstance(vectors, dict) else vectors).size
            if size != emb.dimensions:
                raise SystemExit(
                    f"Collection '{self.collection}' has {size}-d vectors but the "
                    f"configured model produces {emb.dimensions}-d. Qdrant cannot "
                    f"resize in place — re-create the collection and re-index."
                )
            self._ensure_indexes()
            return False

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=emb.dimensions,
                    distance=models.Distance(emb.distance),
                )
            },
            sparse_vectors_config={
                "lexical": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        self._ensure_indexes()
        return True

    def _ensure_indexes(self) -> None:
        # Creating an index that already exists is a no-op in Qdrant, so this
        # stays idempotent.
        for field in _KEYWORD_INDEXES:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="created_at",
            field_schema=models.PayloadSchemaType.DATETIME,
            wait=True,
        )

    # -------------------------------------------------------------- writes
    def delete_document(self, doc_id: str) -> None:
        """Remove every chunk of one document (called before re-writing it)."""
        self._delete_where("doc_id", doc_id)

    def delete_dataset(self, dataset_id: str) -> int:
        """Remove every point belonging to one uploaded dataset.

        A FILTERED delete, never a collection drop (see the module docstring):
        the collection also holds the curated retail corpus and every other
        dataset, so dropping it to forget one upload would destroy all of them.
        Returns the number of points that were there before the delete, which
        is what the caller reports as "removed".
        """
        if not dataset_id:
            raise ValueError("delete_dataset requires a dataset_id")
        # Asserted here as well as in ``_delete_where``, because the count
        # below would otherwise be the first thing to touch a collection this
        # service has no business addressing.
        assert_insightgpt_collection(self.collection)
        before = self.count_dataset(dataset_id)
        self._delete_where("dataset_id", dataset_id)
        return before

    def _delete_where(self, field: str, value: str) -> None:
        """The single delete call site, guarded by the namespace assertion."""
        assert_insightgpt_collection(self.collection)
        if not self.client.collection_exists(self.collection):
            return  # nothing indexed yet; deleting is already satisfied
        self.client.delete(
            collection_name=self.collection,
            wait=True,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key=field, match=models.MatchValue(value=value)
                        )
                    ]
                )
            ),
        )

    def write_document(
        self,
        doc: Document,
        chunks: list[Chunk],
        dense: list[list[float]],
        sparse: list[tuple[list[int], list[float]]] | None,
    ) -> int:
        """Delete-then-write one document's chunks. Returns points written."""
        assert_insightgpt_collection(self.collection)
        self.delete_document(doc.doc_id)
        if not chunks:
            return 0

        now = datetime.now(UTC).isoformat()
        points: list[models.PointStruct] = []
        for i, (chunk, vector) in enumerate(zip(chunks, dense, strict=True)):
            vector_payload: dict[str, Any] = {"dense": vector}
            if sparse is not None:
                indices, values = sparse[i]
                if indices:
                    vector_payload["lexical"] = models.SparseVector(
                        indices=indices, values=values
                    )

            payload: dict[str, Any] = {
                "doc_id": doc.doc_id,
                "source_type": doc.source_type,
                "title": doc.title,
                # Stored as chunked — the breadcrumb header used for embedding is
                # deliberately NOT here, so the user reads back real text (with
                # secrets/PII already redacted upstream).
                "content": chunk.content,
                "chunk_index": i,
                "chunk_total": len(chunks),
                "indexed_at": now,
                "schema_version": self.cfg.schema_version,
            }
            if doc.date:
                payload["created_at"] = doc.date
            for key in ("region", "category", "product_ref", "order_ref", "author_role",
                        "channel", "dataset_id", "dataset_name"):
                value = getattr(doc, key)
                if value is not None:
                    payload[key] = value
            if chunk.heading_path:
                payload["heading_path"] = chunk.heading_path

            points.append(
                models.PointStruct(
                    id=point_id(doc.doc_id, i),
                    vector=vector_payload,
                    payload=payload,
                )
            )

        self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return len(points)

    # --------------------------------------------------------------- reads
    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.get_collection(self.collection).points_count or 0

    def count_dataset(self, dataset_id: str) -> int:
        """Points currently indexed for one dataset.

        This is the number the datasets API reports as ``chunks``, read back
        from Qdrant rather than from whatever the writer believed it wrote.
        """
        if not self.client.collection_exists(self.collection):
            return 0
        result = self.client.count(
            collection_name=self.collection,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="dataset_id", match=models.MatchValue(value=dataset_id)
                    )
                ]
            ),
            exact=True,
        )
        return result.count
