"""Environment overrides on the retrieval config.

These are the settings compose sets and bootstrap acts on. Where the two
disagree the symptom is silent: an embedding model that does not match the
stored vectors returns nonsense rather than an error, and a reranker model
nobody pulled disables reranking on the first 404 with only a stderr line.
"""

from __future__ import annotations

from retrieval.config import RetrievalConfig, load_config


def _cfg(monkeypatch, **env) -> RetrievalConfig:
    for key in ("OLLAMA_HOST", "QDRANT_URL", "EMBED_MODEL", "RERANK_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_config()


def test_defaults_come_from_the_yaml_file(monkeypatch):
    cfg = _cfg(monkeypatch)
    assert cfg.embedding.model == "nomic-embed-text"
    assert cfg.embedding.dimensions == 768
    assert cfg.collection == "documents"
    assert cfg.reranker.model


def test_hosts_are_overridable_and_trailing_slashes_trimmed(monkeypatch):
    cfg = _cfg(
        monkeypatch,
        OLLAMA_HOST="http://ollama:11434/",
        QDRANT_URL="http://qdrant:6333/",
    )
    assert cfg.embedding.base_url == "http://ollama:11434"
    assert cfg.qdrant_url == "http://qdrant:6333"


def test_embed_model_override(monkeypatch):
    assert _cfg(monkeypatch, EMBED_MODEL="other-embed").embedding.model == "other-embed"


def test_rerank_model_override(monkeypatch):
    """The variable bootstrap PULLS must be the variable the reranker USES."""
    cfg = _cfg(monkeypatch, RERANK_MODEL="some/reranker:F16")
    assert cfg.reranker.model == "some/reranker:F16"
    assert cfg.reranker.enabled is True


def test_blank_rerank_model_disables_reranking(monkeypatch):
    # A legitimate choice on a small box: reranking is an enhancement, and
    # turning it off should not leave a model name that 404s on every query.
    cfg = _cfg(monkeypatch, RERANK_MODEL="")
    assert cfg.reranker.model == ""
    assert cfg.reranker.enabled is False
