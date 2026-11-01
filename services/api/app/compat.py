"""Cross-version compatibility helpers (Python 3.10 - 3.14)."""

from __future__ import annotations

from datetime import timezone

try:
    from datetime import UTC  # Python 3.11+
except ImportError:
    UTC = timezone.utc  # noqa: UP017 (Python 3.10 fallback)

__all__ = ["UTC"]
