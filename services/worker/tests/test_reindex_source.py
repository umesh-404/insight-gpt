"""``reindex_docs`` must reindex the REAL corpus, and say so when it cannot.

The failure this guards against is the quiet one: a scheduled reindex that
re-embeds six built-in demo documents, records ``status=success`` with a healthy
chunk count, and leaves the collection with nothing a user ever asked about.
Defaulting to the ingestion corpus is half the fix; refusing to silently
substitute the samples is the other half.

The retrieval package is stubbed here so the test runs with no Qdrant, no
Ollama, and no retrieval install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.config import WorkerSettings
from worker.jobs import JobDependencyError, _resolve_reindex_docs

CORPUS = [
    {
        "doc_id": "TICKET-1",
        "doc_type": "ticket",
        "title": "Late delivery",
        "body": "North backlog.",
        "created_ts": "2026-05-08T09:00:00",
        "author_role": "support_agent",
    }
]


class FakeDocument:
    def __init__(self, raw: dict) -> None:
        self.raw = raw

    @classmethod
    def from_dict(cls, raw: dict) -> FakeDocument:
        return cls(raw)


class FakeModels:
    Document = FakeDocument


class FakeSamples:
    @staticmethod
    def get_sample_documents() -> list[dict]:
        return [{"doc_id": f"SAMPLE-{i}"} for i in range(6)]


class FakeCorpusModule:
    """Stands in for ``retrieval.corpus``."""

    @staticmethod
    def load_corpus(path: Path) -> list[dict]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def state_path_for(path: Path) -> Path:
        return Path(path).parent / ".index_state.json"


@pytest.fixture
def corpus_file(tmp_path) -> Path:
    path = tmp_path / "ingested" / "documents.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(CORPUS), encoding="utf-8")
    return path


def _resolve(settings):
    return _resolve_reindex_docs(settings, FakeModels, FakeCorpusModule, FakeSamples)


def test_default_source_is_the_ingested_corpus():
    assert WorkerSettings().reindex_source == "ingested"


def test_changed_only_is_the_default():
    assert WorkerSettings().reindex_changed_only is True


def test_ingested_source_reads_the_published_corpus(corpus_file):
    docs, origin, state_path = _resolve(
        WorkerSettings(reindex_source="ingested", document_corpus_path=corpus_file)
    )
    assert [d["doc_id"] for d in docs] == ["TICKET-1"]
    assert origin == str(corpus_file)
    assert state_path == corpus_file.parent / ".index_state.json"


def test_missing_corpus_fails_loudly_instead_of_using_samples(tmp_path):
    settings = WorkerSettings(
        reindex_source="ingested", document_corpus_path=tmp_path / "nope.json"
    )
    with pytest.raises(JobDependencyError) as excinfo:
        _resolve(settings)
    message = str(excinfo.value)
    assert "document corpus not found" in message
    # The message must name the way out, both of them.
    assert "ingestion" in message
    assert "REINDEX_SOURCE=samples" in message


def test_samples_are_available_but_only_on_purpose():
    docs, origin, state_path = _resolve(WorkerSettings(reindex_source="samples"))
    assert len(docs) == 6
    assert origin == "built-in sample documents"
    assert state_path is None


def test_an_explicit_path_is_honoured(corpus_file):
    docs, origin, _ = _resolve(WorkerSettings(reindex_source=str(corpus_file)))
    assert len(docs) == 1
    assert origin == str(corpus_file)


def test_an_explicit_missing_path_fails(tmp_path):
    with pytest.raises(JobDependencyError):
        _resolve(WorkerSettings(reindex_source=str(tmp_path / "gone.json")))


def test_corpus_path_matches_the_ingestion_contract():
    # services/ingestion writes here; services/retrieval reads here.
    assert WorkerSettings().document_corpus_path.parts[-3:] == (
        "data",
        "ingested",
        "documents.json",
    )


def test_reindex_source_is_env_driven(monkeypatch):
    monkeypatch.setenv("REINDEX_SOURCE", "samples")
    assert WorkerSettings().reindex_source == "samples"
