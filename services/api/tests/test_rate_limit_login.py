"""Anonymous rate limiting on ``/auth/login``.

``/login`` is reachable without a token, so its limiter must key by client IP
and must never turn a missing bearer token into a 401 — an earlier wiring of
``rate_limit("login")`` did exactly that and broke every authenticated flow in
the suite. These tests pin that behaviour down.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.main import app  # noqa: E402

CREDENTIALS = {"email": "admin@insightgpt.dev", "password": "admin-pass"}


def test_login_works_without_a_bearer_token() -> None:
    """The limiter must not require auth on an anonymous endpoint."""
    with TestClient(app) as client:
        res = client.post("/api/v1/auth/login", json=CREDENTIALS)
    assert res.status_code == 200, res.text
    assert res.json()["access_token"]


def test_login_is_rate_limited_by_ip(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_LOGIN", "2/60")
    deps.reset_rate_limiter()
    try:
        with TestClient(app) as client:
            first = client.post("/api/v1/auth/login", json=CREDENTIALS)
            second = client.post("/api/v1/auth/login", json=CREDENTIALS)
            third = client.post("/api/v1/auth/login", json=CREDENTIALS)

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.headers.get("Retry-After")
        assert third.json()["error"]["code"] == "rate_limited"
    finally:
        deps.reset_rate_limiter()


def test_bad_credentials_still_return_401_not_429(monkeypatch) -> None:
    """A wrong password must fail authentication, not the limiter."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_LOGIN", "5/60")
    deps.reset_rate_limiter()
    try:
        with TestClient(app) as client:
            res = client.post(
                "/api/v1/auth/login",
                json={"email": CREDENTIALS["email"], "password": "wrong"},
            )
        assert res.status_code == 401
    finally:
        deps.reset_rate_limiter()


# ---------------------------------------------------------------------------
# Bucket bookkeeping
# ---------------------------------------------------------------------------


def test_each_distinct_caller_is_tracked_separately():
    """The key is per (bucket, caller): one caller cannot spend another's budget."""
    limiter = deps.TokenBucketLimiter()
    for index in range(50):
        decision = limiter.check(f"login:ip:10.0.0.{index}", capacity=10, window=60.0)
        assert decision.allowed
        # Each caller gets its own full budget, not a share of one.
        assert decision.remaining == 9
    assert limiter.tracked_keys() == 50


def test_idle_buckets_are_swept_once_the_sweep_threshold_is_crossed():
    """Insertions amortise the sweep, so the dict cannot grow without bound."""
    limiter = deps.TokenBucketLimiter()
    # Partially drain far more distinct callers than the sweep threshold.
    total = deps.TokenBucketLimiter._SWEEP_EVERY * 3
    for index in range(total):
        assert limiter.check(f"read:user:{index}", capacity=120, window=60.0).allowed
    # Every bucket is young, so nothing is evicted yet: the sweep is time-based,
    # and this asserts it does not drop live budgets just because it ran.
    assert limiter.tracked_keys() == total

    # Age every entry past the horizon, then trigger one more sweep.
    with limiter._lock:
        limiter._buckets = {
            key: (tokens, stamp - 10_000.0) for key, (tokens, stamp) in limiter._buckets.items()
        }
        limiter._since_sweep = deps.TokenBucketLimiter._SWEEP_EVERY - 1
    assert limiter.check("read:user:fresh", capacity=120, window=60.0).allowed
    # The stale entries are gone; only the live caller may remain.
    assert limiter.tracked_keys() <= 1


def test_exhausted_bucket_still_reports_retry_after():
    """Eviction must not let a drained caller slip back to a full budget."""
    limiter = deps.TokenBucketLimiter()
    for _ in range(3):
        assert limiter.check("ask:user:u1", capacity=3, window=60.0).allowed
    denied = limiter.check("ask:user:u1", capacity=3, window=60.0)
    assert not denied.allowed
    assert denied.remaining == 0
    assert denied.retry_after >= 1
    # Still tracked — forgetting it here is exactly the bug eviction must avoid.
    assert limiter.tracked_keys() == 1
