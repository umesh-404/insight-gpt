"""Qdrant writes and collection setup: dense + sparse, delete-then-write.

The single ``documents`` collection holds every source type; ``source_type`` is
a payload field, not a separate collection, so one query can span types or
filter to one (docs/04-retrieval-rag.md §7).

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

from datetime import UTC, datetime
from typing import Any

from qdrant_client import QdrantClient, models

from .config import RetrievalConfig
from .ids import point_id
from .models import Chunk, Document

# Payload fields indexed for filtering / diversity (docs/04 §1.1). Unindexed
# fields are still stored and returned — an index is only needed to FILTER.
_KEYWORD_INDEXES = ("source_type", "region", "category", "product_ref", "order_ref", "author_role")


class Store:
    def __init__(self, cfg: RetrievalConfig) -> None:
        self.cfg = cfg
        self.collection = cfg.collection
        self.client = QdrantClient(url=cfg.qdrant_url, timeout=120)

    # -------------------------------------------------------------- setup
    def ensure_collection(self) -> bool:
        """Create the ``documents`` collection idempotently. Returns True if created.

        Dense vector ``dense`` (Ollama embedding) + sparse vector ``lexical``
        (term frequency, ``modifier: idf`` so Qdrant applies inverse-document-
        frequency at query time). Vector config is fixed at creation, so the
        sparse slot is declared now even before it is populated.
        """
        emb = self.cfg.embedding
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
        self.client.delete(
            collection_name=self.collection,
            wait=True,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id", match=models.MatchValue(value=doc_id)
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
            for key in ("region", "category", "product_ref", "order_ref", "author_role", "channel"):
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
