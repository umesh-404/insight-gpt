"""The reasoning provider's production contract.

Three properties matter more than any other here, and all three are about what
happens when the model is *not* behaving:

1. A transient fault is retried; a refusal is not.
2. A failure raises rather than returning something that reads like an answer.
3. A misconfigured deployment dies at startup, not at a user's first question.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import ProviderError  # noqa: E402
from app.providers.factory import (  # noqa: E402
    get_provider,
    verify_provider_config,
)
from app.providers.openai_compatible import (  # noqa: E402
    OpenAICompatibleProvider,
    _sleep_for,
    _sse_delta,
)

BASE = "https://example.invalid/v1"


def _provider(handler, **kw) -> OpenAICompatibleProvider:
    """A provider whose transport is a scripted mock, so no network is touched."""
    p = OpenAICompatibleProvider(
        name="gemini", model="test-model", api_key="k", base_url=BASE, **kw
    )
    p._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer k"},
    )
    return p


def _ok(text: str = "hello") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    )


# --- retry behaviour ---------------------------------------------------------
def test_a_transient_failure_is_retried_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return _ok("recovered")

    assert _provider(handler).complete("hi") == "recovered"
    assert calls["n"] == 3


def test_a_rejected_key_is_not_retried(monkeypatch) -> None:
    """401 is a verdict, not a fault: retrying burns latency to be told twice."""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(ProviderError) as err:
        _provider(handler).complete("hi")
    assert calls["n"] == 1, "a 401 must not be retried"
    assert err.value.retryable is False
    assert "API key" in str(err.value)


def test_retries_are_bounded_and_then_raise(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    with pytest.raises(ProviderError) as err:
        _provider(handler, max_retries=2).complete("hi")
    assert calls["n"] == 3, "one attempt plus two retries"
    assert err.value.retryable is True


def test_an_unknown_model_names_the_setting_to_change(monkeypatch) -> None:
    """Catalogues get retired; the error has to say what to edit."""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    with pytest.raises(ProviderError) as err:
        _provider(handler).complete("hi")
    assert "LLM_MODEL" in str(err.value)
    assert err.value.retryable is False


def test_a_timeout_raises_rather_than_returning_text(monkeypatch) -> None:
    """The failure that matters most: never answer when the model never spoke."""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(ProviderError):
        _provider(handler, max_retries=1).complete("hi")


def test_an_empty_completion_is_a_failure_not_an_answer(monkeypatch) -> None:
    """A reasoning model can spend its whole budget thinking and return null."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    with pytest.raises(ProviderError) as err:
        _provider(handler).complete("hi")
    assert "token budget" in str(err.value)


# --- request construction ----------------------------------------------------
def test_the_output_budget_is_sent_under_exactly_one_name() -> None:
    """An unbounded completion is an unbounded bill - but one ceiling, one name.

    Sending `max_tokens` and `max_completion_tokens` together to cover both
    spellings is not belt-and-braces: Gemini answers 400 "max_tokens and
    max_completion_tokens cannot both be set", which would fail every call in
    production. Confirmed against the live endpoint.
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _ok()

    _provider(handler, max_output_tokens=321).complete("hi")
    assert seen["max_tokens"] == 321
    assert "max_completion_tokens" not in seen


def test_the_budget_field_can_be_switched() -> None:
    """OpenAI's newer reasoning models accept only `max_completion_tokens`."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _ok()

    _provider(
        handler, max_output_tokens=99, max_tokens_field="max_completion_tokens"
    ).complete("hi")
    assert seen["max_completion_tokens"] == 99
    assert "max_tokens" not in seen


def test_the_api_key_never_appears_in_an_error(monkeypatch) -> None:
    """The error text reaches the browser through the error envelope."""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)
    secret = "sk-DO-NOT-LEAK-6f1a2b"

    def handler(request: httpx.Request) -> httpx.Response:
        # Providers do echo request context back in error bodies, so the worst
        # case is the key arriving from the other side rather than from us.
        return httpx.Response(400, json={"error": {"message": f"bad request {secret}"}})

    provider = OpenAICompatibleProvider(
        name="gemini", model="m", api_key=secret, base_url=BASE
    )
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as err:
        provider.complete("hi")
    assert secret not in str(err.value)
    assert "Bearer" not in str(err.value)


# --- backoff -----------------------------------------------------------------
def test_backoff_honours_retry_after_but_refuses_an_absurd_one() -> None:
    assert _sleep_for(1, "3") == 3.0
    # An hour is an outage, not a throttle; waiting it out holds the request
    # open far past the client's own timeout.
    assert _sleep_for(1, "3600") <= 8.0
    assert _sleep_for(1, "not-a-number") <= 8.0
    # Full jitter: two waits for the same attempt should not be identical.
    assert len({_sleep_for(4, None) for _ in range(20)}) > 1


# --- streaming ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('data: {"choices":[{"delta":{"content":"hi"}}]}', "hi"),
        ("data: [DONE]", ""),
        ("", ""),
        (": keep-alive", ""),
        ('data: {"choices":[{"delta":{}}]}', ""),      # finish-reason-only chunk
        ("data: {not json", ""),                        # malformed, must not raise
        ('data: {"choices":[]}', ""),
    ],
)
def test_sse_parsing_tolerates_real_streams(line: str, expected: str) -> None:
    """Compat layers do not document their wire format; a stray line is normal
    traffic, not a reason to abort an answer already on screen."""
    assert _sse_delta(line) == expected


def test_a_list_wrapped_error_body_is_still_read(monkeypatch) -> None:
    """Gemini wraps its error object in a LIST.

    The naive `response.json()["error"]` raises on that and throws the message
    away, so an invalid key reported as a bare "HTTP 400" with the one useful
    sentence - "Please pass a valid API key" - discarded. Verified against the
    live endpoint, not assumed.
    """
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json=[{"error": {"code": 400, "message": "Please pass a valid API key"}}]
        )

    with pytest.raises(ProviderError) as err:
        _provider(handler).complete("hi")
    assert "Please pass a valid API key" in str(err.value)
    assert err.value.retryable is False


def test_an_object_wrapped_error_body_still_works() -> None:
    """The OpenAI shape must keep working alongside the Gemini one."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    with pytest.raises(ProviderError) as err:
        _provider(handler).complete("hi")
    assert "bad model" in str(err.value)


# --- health check ------------------------------------------------------------
def _models(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


def test_health_check_catches_a_retired_model() -> None:
    """The failure this layer is most exposed to.

    A model id valid at deploy time stops existing on a published schedule.
    Reporting the provider healthy because a key is set stays green straight
    through that, while every question 503s.
    """
    p = _provider(lambda r: _models("gemini-9-future", "other-model"))
    ok, detail = p.health_check()
    assert ok is False
    assert "LLM_MODEL" in detail and "test-model" in detail


def test_health_check_passes_on_a_listed_model() -> None:
    ok, detail = _provider(lambda r: _models("test-model")).health_check()
    assert ok is True
    assert "available" in detail


def test_health_check_tolerates_a_prefixed_model_id() -> None:
    """Providers prefix ids inconsistently ("models/gemini-2.5-flash")."""
    ok, _ = _provider(lambda r: _models("models/test-model")).health_check()
    assert ok is True


def test_health_check_reports_a_revoked_key() -> None:
    ok, detail = _provider(lambda r: httpx.Response(401)).health_check()
    assert ok is False
    assert "key" in detail


def test_health_check_never_raises_on_an_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    ok, detail = _provider(handler).health_check()
    assert ok is False
    assert "unreachable" in detail


# --- startup configuration ---------------------------------------------------
def _env(monkeypatch, **kw) -> None:
    for name in ("APP_ENV", "LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for key, value in kw.items():
        monkeypatch.setenv(key, value)


def test_production_refuses_the_template_provider(monkeypatch) -> None:
    """The template provider produces prose that looks grounded and is not."""
    _env(monkeypatch, APP_ENV="production", LLM_PROVIDER="fake")
    with pytest.raises(RuntimeError, match="cannot serve users"):
        verify_provider_config()


def test_a_cloud_provider_without_its_key_fails_at_startup(monkeypatch) -> None:
    _env(monkeypatch, APP_ENV="production", LLM_PROVIDER="gemini")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        verify_provider_config()


def test_a_configured_cloud_provider_passes(monkeypatch) -> None:
    _env(
        monkeypatch,
        APP_ENV="production",
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="x",
        LLM_MODEL="gemini-2.5-flash",
    )
    verify_provider_config()


def test_an_unknown_provider_names_the_valid_ones(monkeypatch) -> None:
    _env(monkeypatch, LLM_PROVIDER="jev")
    with pytest.raises(RuntimeError, match="gemini"):
        verify_provider_config()


def test_the_retired_provider_is_gone(monkeypatch) -> None:
    """`jev`/`typesafe` was removed; selecting it must not resurrect anything."""
    _env(monkeypatch, LLM_PROVIDER="typesafe")
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_provider()
    assert not os.path.exists(
        Path(__file__).resolve().parents[1] / "app" / "providers" / "typesafe_provider.py"
    )


def test_limits_come_from_the_environment(monkeypatch) -> None:
    _env(monkeypatch, LLM_PROVIDER="gemini", GEMINI_API_KEY="x")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "123")
    monkeypatch.setenv("LLM_MAX_RETRIES", "7")
    provider = get_provider()
    assert provider.max_output_tokens == 123
    assert provider.max_retries == 7


def test_a_nonsense_limit_falls_back_instead_of_crashing(monkeypatch) -> None:
    _env(monkeypatch, LLM_PROVIDER="gemini", GEMINI_API_KEY="x")
    monkeypatch.setenv("LLM_MAX_RETRIES", "lots")
    assert get_provider().max_retries == 3
