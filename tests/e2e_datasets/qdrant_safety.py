"""The one guard that stands between this test suite and another project's data.

The Qdrant on 6333 is a **shared** instance. It already holds ``code``, ``docs``
and ``memory``, which belong to a different project on this machine and are not
recoverable if dropped. Test code is exactly where "just reset the collection"
gets written, so the prefix check lives here, in code, and every destructive
call in this package and in ``scripts/demo_check.py`` goes through it.

Two rules, both enforced by :func:`assert_test_collection`:

* a collection this suite may create, write to or delete must be named
  ``insightgpt_test_*``;
* anything else — including InsightGPT's own production ``insightgpt_documents``
  — is refused, because a test has no business dropping it either.

``insightgpt_documents`` is deliberately NOT allowed through this guard. The
suite reads it (counting points, checking a payload) but never deletes it; a
dataset created against it is removed through the API's own filtered delete on
``dataset_id``, which touches points and never the collection.
"""

from __future__ import annotations

import os
import re

#: Only collections matching this may be created or dropped by tests.
TEST_COLLECTION_RE = re.compile(r"^insightgpt_test_[A-Za-z0-9_]+$")

#: Never touched, under any circumstance. Listed so the failure message can say
#: WHY rather than only "refused".
FOREIGN_COLLECTIONS = ("code", "docs", "memory")


class UnsafeCollectionError(RuntimeError):
    """Raised instead of performing a destructive call on a collection that is
    not demonstrably one of this suite's own scratch collections."""


def assert_test_collection(name: str) -> str:
    """Return ``name`` if it is a scratch collection this suite owns, else raise.

    Called immediately before every create/delete. It is intentionally stricter
    than the contract's ``insightgpt_`` prefix: a test may only ever address its
    own ``insightgpt_test_*`` scratch space.
    """
    if not isinstance(name, str) or not TEST_COLLECTION_RE.match(name):
        raise UnsafeCollectionError(
            f"Refusing a destructive Qdrant call against {name!r}. This instance is "
            f"shared with another project (it holds {', '.join(FOREIGN_COLLECTIONS)}), "
            "so tests may only create or delete collections named "
            "'insightgpt_test_<name>'."
        )
    return name


def scratch_collection_name(suffix: str) -> str:
    """Build a scratch collection name, unique to this process.

    The pid keeps two concurrent runs (or a demo check running beside pytest)
    from sharing a collection and deleting each other's points.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", suffix).strip("_").lower() or "scratch"
    return assert_test_collection(f"insightgpt_test_{cleaned}_{os.getpid()}")


def qdrant_url() -> str:
    return os.environ.get("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")


def drop_test_collection(name: str, *, url: str | None = None, timeout: float = 30.0) -> bool:
    """Delete one scratch collection. Asserts the name first, always.

    Returns True when Qdrant reported the delete, False when the collection was
    not there. Any other transport failure is swallowed: a test tearing down
    must not turn a cleanup hiccup into a test failure, and a leftover
    ``insightgpt_test_*`` collection is harmless.
    """
    assert_test_collection(name)
    import httpx

    try:
        response = httpx.delete(f"{url or qdrant_url()}/collections/{name}", timeout=timeout)
    except (httpx.HTTPError, OSError):
        return False
    return response.status_code < 300


def list_collections(*, url: str | None = None, timeout: float = 5.0) -> list[str]:
    """Names of every collection on the instance (read-only)."""
    import httpx

    response = httpx.get(f"{url or qdrant_url()}/collections", timeout=timeout)
    response.raise_for_status()
    return [c["name"] for c in response.json()["result"]["collections"]]
