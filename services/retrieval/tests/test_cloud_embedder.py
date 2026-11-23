"""The cloud embedding backend, and the two traps it exists to avoid.

Both failures this file guards are *silent* ones: wrong-width vectors written
into a collection built for another width, and nomic's asymmetric prefixes
carried into a model that does not use them. Neither raises on its own; both
just quietly cost recall, which is why they are asserted rather than trusted.
"""

from __future__ import annotations

import httpx
import pytest

from retrieval.cloud_embedder import CloudEmbedder, scrub
from retrieval.config import EmbeddingConfig
from retrieval.embedder import Embedder, EmbeddingError, make_embedder

KEY = "AIzaTestKeyNotReal1234567890"


def _cfg(**kw) -> EmbeddingConfig:
    base = {
        "provider": "openai",
        "api_key_env": "TEST_EMBED_KEY",
        "base_url": "https://example.invalid/v1",
        "model": "gemini-embedding-001",
        "dimensions": 4,
        "query_prefix": "",
        "document_prefix": "",
    }
    base.update(kw)
    return EmbeddingConfig(**base)


def _embedder(monkeypatch, handler, **kw) -> CloudEmbedder:
    monkeypatch.setenv("TEST_EMBED_KEY", KEY)
    e = CloudEmbedder(_cfg(**kw))
    e._client = httpx.Client(transport=httpx.MockTransport(handler))
    return e


def _vectors(*rows: list[float]) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"embedding": r} for r in rows]})


# --- selection ---------------------------------------------------------------
def test_the_factory_defaults_to_the_local_backend() -> None:
    """A purely local install must be completely unaffected by this addition."""
    assert isinstance(make_embedder(EmbeddingConfig()), Embedder)
    assert isinstance(make_embedder(EmbeddingConfig(provider="ollama")), Embedder)


def test_the_factory_selects_the_cloud_backend(monkeypatch) -> None:
    monkeypatch.setenv("TEST_EMBED_KEY", KEY)
    assert isinstance(make_embedder(_cfg()), CloudEmbedder)


def test_a_missing_key_fails_at_construction(monkeypatch) -> None:
    """Not at the first embed call, halfway through indexing a corpus."""
    monkeypatch.delenv("TEST_EMBED_KEY", raising=False)
    with pytest.raises(EmbeddingError, match="TEST_EMBED_KEY"):
        CloudEmbedder(_cfg())


# --- the dimension trap ------------------------------------------------------
def test_a_width_mismatch_fails_loudly_with_the_number_to_set(monkeypatch) -> None:
    """Writing 3072-d vectors into a 768-d collection corrupts it silently."""
    e = _embedder(monkeypatch, lambda r: _vectors([0.1] * 3072))
    with pytest.raises(EmbeddingError) as err:
        e.embed_documents(["hello"])
    assert "3072" in str(err.value)
    assert "EMBED_DIMENSIONS=3072" in str(err.value), "the fix must be in the message"


def test_a_matching_width_passes(monkeypatch) -> None:
    e = _embedder(monkeypatch, lambda r: _vectors([0.1, 0.2, 0.3, 0.4]))
    assert e.embed_documents(["hello"]) == [[0.1, 0.2, 0.3, 0.4]]


def test_dimensions_are_not_requested_unless_asked(monkeypatch) -> None:
    """A provider that does not know the field can reject the whole request."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.update(json.loads(request.content))
        return _vectors([0.0] * 4)

    _embedder(monkeypatch, handler).embed_documents(["x"])
    assert "dimensions" not in seen

    seen.clear()
    _embedder(monkeypatch, handler, request_dimensions=True).embed_documents(["x"])
    assert seen["dimensions"] == 4


# --- the prefix trap ---------------------------------------------------------
def test_no_prefix_is_added_for_a_cloud_model(monkeypatch) -> None:
    """`search_document: ` belongs to nomic and to nothing else.

    Sent to a cloud model it does not error - it embeds those literal words
    into every passage and costs recall with no visible symptom.
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.update(json.loads(request.content))
        return _vectors([0.0] * 4)

    e = _embedder(monkeypatch, handler)
    e.embed_documents(["the north region"])
    assert seen["input"] == ["the north region"]
    e.embed_query("why did sales fall")
    assert seen["input"] == ["why did sales fall"]


def test_the_config_clears_prefixes_for_a_cloud_provider(monkeypatch) -> None:
    """The guard belongs in config so a caller cannot forget it."""
    for name in ("EMBED_QUERY_PREFIX", "EMBED_DOCUMENT_PREFIX", "OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBED_PROVIDER", "openai")
    from retrieval.config import load_config

    cfg = load_config()
    assert cfg.embedding.query_prefix == ""
    assert cfg.embedding.document_prefix == ""


def test_ollama_host_does_not_hijack_a_cloud_base_url(monkeypatch) -> None:
    """Compose sets OLLAMA_HOST unconditionally.

    Without the ordering guard, a cloud embedding deployment would keep
    pointing at a model server that is not running.
    """
    monkeypatch.setenv("EMBED_PROVIDER", "openai")
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    from retrieval.config import load_config

    cfg = load_config()
    assert "11434" not in cfg.embedding.base_url
    assert cfg.embedding.base_url.startswith("https://")


# --- failure handling --------------------------------------------------------
def test_a_rate_limit_is_retried(monkeypatch) -> None:
    monkeypatch.setattr("retrieval.cloud_embedder.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _vectors([0.0] * 4) if calls["n"] > 2 else httpx.Response(429)

    assert _embedder(monkeypatch, handler).embed_documents(["x"])
    assert calls["n"] == 3


def test_a_rejected_request_is_not_retried(monkeypatch) -> None:
    """A 400 will be a 400 again; retrying only delays the error."""
    monkeypatch.setattr("retrieval.cloud_embedder.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    with pytest.raises(EmbeddingError):
        _embedder(monkeypatch, handler).embed_documents(["x"])
    assert calls["n"] == 1


def test_the_providers_own_message_survives(monkeypatch) -> None:
    """"Please pass a valid API key" tells an operator what to do.

    "Client error '400 Bad Request' for url ..." does not. Gemini wraps its
    error object in a LIST, which makes the obvious `.json()["error"]` raise
    and discard the text, so both shapes are asserted.
    """
    monkeypatch.setattr("retrieval.cloud_embedder.time.sleep", lambda _: None)

    def listy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=[{"error": {"message": "Please pass a valid API key"}}])

    with pytest.raises(EmbeddingError, match="Please pass a valid API key"):
        _embedder(monkeypatch, listy).embed_documents(["x"])

    def objecty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "unknown model"}})

    with pytest.raises(EmbeddingError, match="unknown model"):
        _embedder(monkeypatch, objecty).embed_documents(["x"])


def test_the_key_never_reaches_the_error_text(monkeypatch) -> None:
    monkeypatch.setattr("retrieval.cloud_embedder.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed talking to host with {KEY}")

    with pytest.raises(EmbeddingError) as err:
        _embedder(monkeypatch, handler).embed_documents(["x"])
    assert KEY not in str(err.value)


def test_scrub_leaves_short_strings_alone() -> None:
    """A 'key' short enough to appear inside ordinary words is not one."""
    assert scrub("a valid API key", "k") == "a valid API key"
    assert scrub(f"token {KEY} here", KEY) == "token [redacted] here"


def test_health_names_the_setting_when_the_model_is_gone(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "some-other-model"}]})

    with pytest.raises(EmbeddingError, match="EMBED_MODEL"):
        _embedder(monkeypatch, handler).health()


def test_health_accepts_a_prefixed_model_id(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "models/gemini-embedding-001"}]})

    assert _embedder(monkeypatch, handler).health() == "gemini-embedding-001"


def test_query_embeddings_are_memoised(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _vectors([0.0] * 4)

    e = _embedder(monkeypatch, handler)
    e.embed_query("same question")
    e.embed_query("same question")
    assert calls["n"] == 1
