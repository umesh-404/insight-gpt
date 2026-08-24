"""Shared, cached dependencies: the catalog, the insight engine, app metadata.

The engine is built once and reused. It is config-driven (doc 06 §3.1): with the
default settings it uses the offline fixture stack (fake/Ollama provider + the
in-process DuckDB warehouse); pointing ``WAREHOUSE`` at a Postgres DSN later
swaps the executor without changing any router.

This module also owns the in-process **rate limiter** (doc 06 §8): a token
bucket keyed by user id (falling back to the client IP), tiered per bucket and
scaled per role, applied as a dependency on the expensive endpoints.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, Request, Response

from ..auth.roles import current_claims, optional_claims
from ..auth.tokens import TokenClaims
from ..config import Settings, get_settings
from ..engine.build import build_engine
from ..engine.engine import InsightEngine
from ..semantic.catalog import SemanticCatalog, load_catalog
from ..warehouse.executor import Warehouse
from .errors import RateLimitedError

APP_VERSION = "0.1.0"
_STARTED_AT = time.time()


def uptime_seconds() -> float:
    return round(time.time() - _STARTED_AT, 3)


@lru_cache(maxsize=1)
def get_catalog() -> SemanticCatalog:
    return load_catalog()


@lru_cache(maxsize=1)
def _build_engine() -> InsightEngine:
    # Config-driven: offline fixture stack by default; real Postgres/Qdrant
    # backends when WAREHOUSE/RETRIEVER select them (see engine/build.py).
    return build_engine(get_settings())


def get_engine() -> InsightEngine:
    return _build_engine()


def get_warehouse() -> Warehouse:
    return get_engine().warehouse


def app_settings() -> Settings:
    return get_settings()


def reset_caches() -> None:
    """Drop cached singletons (used by tests that flip env before building)."""
    _build_engine.cache_clear()
    get_catalog.cache_clear()
    reset_rate_limiter()


# --------------------------------------------------------------------------
# Rate limiting (doc 06 §8)
# --------------------------------------------------------------------------

# bucket -> (requests, window seconds). Overridable per bucket via
# ``RATE_LIMIT_<BUCKET>`` as ``"20/60"`` (or a bare ``"20"``, meaning per minute).
_DEFAULT_LIMITS: dict[str, tuple[int, float]] = {
    "ask": (20, 60.0),      # POST /ask, POST /reports — the expensive LLM paths
    "read": (120, 60.0),    # dashboards, metrics, conversations
    "mutate": (30, 60.0),   # sources, pipeline triggers, feedback
    "login": (10, 60.0),    # POST /auth/login, keyed by IP
}

# Roles are additive in capability, so their budgets are too.
_ROLE_MULTIPLIER = {"viewer": 1.0, "analyst": 2.0, "admin": 4.0}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def rate_limiting_enabled() -> bool:
    """``RATE_LIMIT_ENABLED``; off by default under pytest so tests are stable."""
    under_test = "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ
    return _env_flag("RATE_LIMIT_ENABLED", default=not under_test)


def bucket_limit(bucket: str) -> tuple[int, float]:
    """Resolve ``(requests, window_seconds)`` for a bucket, honouring env."""
    requests, window = _DEFAULT_LIMITS.get(bucket, _DEFAULT_LIMITS["read"])
    raw = os.getenv(f"RATE_LIMIT_{bucket.upper()}")
    if raw:
        head, _, tail = raw.partition("/")
        try:
            requests = int(head.strip())
            if tail.strip():
                window = float(tail.strip())
        except ValueError:  # a malformed override must not take the API down
            return _DEFAULT_LIMITS.get(bucket, _DEFAULT_LIMITS["read"])
    return max(1, requests), max(1.0, window)


@dataclass
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class TokenBucketLimiter:
    """A tiny thread-safe token bucket. No dependencies, no shared state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (tokens, last refill monotonic timestamp)
        self._buckets: dict[str, tuple[float, float]] = {}

    def check(self, key: str, capacity: int, window: float) -> Decision:
        rate = capacity / window
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(capacity), now))
            tokens = min(float(capacity), tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return Decision(True, capacity, int(tokens - 1.0), 0)
            self._buckets[key] = (tokens, now)
            retry_after = max(1, int((1.0 - tokens) / rate) + 1)
            return Decision(False, capacity, 0, retry_after)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_LIMITER = TokenBucketLimiter()


def get_limiter() -> TokenBucketLimiter:
    return _LIMITER


def reset_rate_limiter() -> None:
    _LIMITER.reset()


def _client_key(request: Request, claims: TokenClaims | None) -> str:
    if claims is not None and claims.sub:
        return f"user:{claims.sub}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def rate_limit(bucket: str, *, require_auth: bool = True):
    """Return a dependency enforcing ``bucket``'s limit for the calling user.

    Emits ``X-RateLimit-Limit``/``X-RateLimit-Remaining`` on success and raises
    a ``429 rate_limited`` with ``Retry-After`` on exhaustion.

    ``require_auth=False`` is for endpoints reachable anonymously (``/login``):
    the caller is then keyed by client IP instead of user id, and a missing
    token must not turn into a 401 from the limiter itself.
    """

    claims_dep = current_claims if require_auth else optional_claims

    async def dep(
        request: Request,
        response: Response,
        claims: TokenClaims | None = Depends(claims_dep),
    ) -> None:
        if not rate_limiting_enabled():
            return
        capacity, window = bucket_limit(bucket)
        role = claims.role if claims is not None else "viewer"
        capacity = max(1, int(capacity * _ROLE_MULTIPLIER.get(role, 1.0)))
        decision = _LIMITER.check(
            f"{bucket}:{_client_key(request, claims)}", capacity, window
        )
        if not decision.allowed:
            raise RateLimitedError(
                "Rate limit exceeded; slow down.",
                details={"bucket": bucket, "limit": capacity, "window_s": window},
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(capacity),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response.headers["X-RateLimit-Limit"] = str(capacity)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)

    return dep
