"""Ingestion settings — env-driven, no secrets in the repo (``docs/03``).

The Postgres DSN (which carries the password) comes from the environment; this
only names non-secret locations and the raw schema. Mirrors the variables in
``.env.example``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Real warehouse DSN; when unset the loader degrades to a counted no-op.
    postgres_dsn: str | None = None
    raw_schema: str = "raw"

    # Where the generator wrote its output.
    generated_dir: Path = _REPO_ROOT / "data" / "generated"

    # Where the redacted document corpus is published for services/retrieval to
    # index. This file IS the ingestion -> retrieval hand-off contract; the
    # worker's `reindex_docs` job reads exactly this path by default.
    document_corpus_path: Path = _REPO_ROOT / "data" / "ingested" / "documents.json"


def get_settings() -> IngestionSettings:
    return IngestionSettings()
