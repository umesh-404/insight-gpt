"""Deterministic point ids — the idea that makes re-indexing safe.

    id = uuid5(NAMESPACE, "doc_id:chunk_index")

Because the id is a pure function of ``(doc_id, chunk_index)``, re-indexing a
document overwrites its chunks in place instead of appending duplicates. Without
this, every re-index would double the corpus and searches would fill up with
stale copies of the same text.

Kept in its own module (no Qdrant import) so id generation is unit-testable
offline and so both the store and any tooling derive ids the same single way.
"""

from __future__ import annotations

import uuid

# Fixed namespace: ids must be identical across runs and machines. Generated
# once and hardcoded on purpose — never regenerate it, or every existing point
# becomes unreachable.
NAMESPACE = uuid.UUID("b6a1f2c4-7d3e-5a90-9c21-4e8f0d6b3a17")


def point_id(doc_id: str, chunk_index: int) -> str:
    """Stable Qdrant point id for one chunk of one document."""
    return str(uuid.uuid5(NAMESPACE, f"{doc_id}:{chunk_index}"))
