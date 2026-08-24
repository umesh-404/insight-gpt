"""The canonical document schema — one normalizer for every producer.

Retrieval is the *consumer* at the end of the hand-off, so it owns the schema:
whatever a producer calls a field, this module maps it onto the exact keys the
store writes into Qdrant and the engine filters on. Nothing downstream ever
sees a producer's spelling.

Two producers exist today and they disagree on names, which is precisely why
this file exists:

* ``services/ingestion`` exports the redacted corpus straight from the
  generator's documents (``doc_type``, ``created_ts``, ``author_role`` values
  like ``support_agent``);
* ``retrieval.sample_docs`` uses the engine-facing spelling already
  (``source_type``, ``date``, ``author_role`` = ``agent``).

Canonical keys (what lands in the Qdrant payload)::

    doc_id, source_type, title, body, created_at,
    region, category, product_ref, order_ref, author_role, channel

``author_role`` is a closed enum — ``customer | agent | manager`` — because the
engine and the UI filter on it; an un-normalized ``support_agent`` would simply
never match and would fail silently. ``source_type`` is normalized the same way
for the same reason.
"""

from __future__ import annotations

import hashlib
import json

# --- closed enums --------------------------------------------------------------
AUTHOR_ROLES: tuple[str, ...] = ("customer", "agent", "manager")
SOURCE_TYPES: tuple[str, ...] = ("ticket", "review", "report", "email")

_ROLE_ALIASES: dict[str, str] = {
    "customer": "customer",
    "buyer": "customer",
    "reviewer": "customer",
    "shopper": "customer",
    "agent": "agent",
    "support": "agent",
    "support_agent": "agent",
    "service_agent": "agent",
    "csr": "agent",
    "manager": "manager",
    "ops_manager": "manager",
    "operations_manager": "manager",
    "analyst": "manager",
}

_TYPE_ALIASES: dict[str, str] = {
    "ticket": "ticket",
    "tickets": "ticket",
    "support_ticket": "ticket",
    "review": "review",
    "reviews": "review",
    "product_review": "review",
    "report": "report",
    "reports": "report",
    "business_report": "report",
    "email": "email",
    "emails": "email",
    # A plain .txt/.md drop-file has no type of its own; treat it as a report so
    # it is still filterable rather than landing under an unmatched label.
    "text": "report",
    "document": "report",
}

# Producer key -> canonical key. First present alias wins, in this order.
_SOURCE_TYPE_KEYS = ("source_type", "doc_type", "type")
_CREATED_AT_KEYS = ("created_at", "created_ts", "date", "timestamp")
_PRODUCT_REF_KEYS = ("product_ref", "product_sku", "sku", "product_id")
_ORDER_REF_KEYS = ("order_ref", "order_id")
_DOC_ID_KEYS = ("doc_id", "id", "ticket_id", "review_id", "report_id")

# Metadata carried through verbatim once found under any of its aliases.
CANONICAL_FIELDS: tuple[str, ...] = (
    "doc_id",
    "source_type",
    "title",
    "body",
    "created_at",
    "region",
    "category",
    "product_ref",
    "order_ref",
    "author_role",
    "channel",
)

# The hash covers exactly what the index depends on: if none of these changed,
# re-embedding the document would produce byte-identical points.
_HASHED_FIELDS: tuple[str, ...] = CANONICAL_FIELDS

CONTENT_HASH_KEY = "_content_hash"


def _first(raw: dict, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return None


def normalize_author_role(value: object | None) -> str | None:
    """Map any producer's role onto the ``customer|agent|manager`` enum.

    An unrecognized role returns ``None`` rather than a made-up value: a missing
    filter field is honest, a wrong one is a silent mismatch.
    """
    if value is None:
        return None
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return _ROLE_ALIASES.get(key)


def normalize_source_type(value: object | None) -> str:
    """Map any producer's document type onto the ``source_type`` enum.

    Falls back to the lowercased original so an unforeseen type is still stored
    and still filterable, just not one of the four known ones.
    """
    if value is None:
        return "document"
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return _TYPE_ALIASES.get(key, key)


def _canonical_values(raw: dict) -> dict:
    """The canonical fields of one producer dict, without the hash."""
    return {
        "doc_id": str(_first(raw, _DOC_ID_KEYS) or ""),
        "source_type": normalize_source_type(_first(raw, _SOURCE_TYPE_KEYS)),
        "title": str(raw.get("title") or ""),
        "body": str(raw.get("body") or raw.get("text") or ""),
        "created_at": _as_str(_first(raw, _CREATED_AT_KEYS)),
        "region": _as_str(raw.get("region")),
        "category": _as_str(raw.get("category")),
        # Refs are keyword-indexed in Qdrant, so they must be strings even when
        # the producer had an integer id.
        "product_ref": _as_str(_first(raw, _PRODUCT_REF_KEYS)),
        "order_ref": _as_str(_first(raw, _ORDER_REF_KEYS)),
        "author_role": normalize_author_role(raw.get("author_role")),
        "channel": _as_str(raw.get("channel")),
    }


def normalize_document(raw: dict) -> dict:
    """Map one producer document dict onto the canonical schema.

    Returns a dict with every canonical key present (``None`` where unknown),
    plus ``_content_hash`` computed over the CANONICAL values.

    The hash is deliberately recomputed rather than trusting a producer's
    ``_content_hash``: it is what decides whether a document gets re-embedded,
    so it must be a function of what actually gets embedded. A producer's own
    hash is its own business (ingestion uses one to decide whether to rewrite
    its corpus file) and the two never have to agree.
    """
    doc = _canonical_values(raw)
    doc[CONTENT_HASH_KEY] = _hash_values(doc)
    return doc


def content_hash(doc: dict) -> str:
    """Stable hash of a document's index-relevant content.

    Accepts either a producer dict or an already-canonical one: it canonicalizes
    first, so both sides of the hand-off compute the same value for the same
    document no matter which spelling they hold it in.
    """
    return _hash_values(_canonical_values(doc))


def _hash_values(canonical: dict) -> str:
    payload = {f: canonical.get(f) for f in _HASHED_FIELDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _as_str(value: object | None) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
