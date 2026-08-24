"""Worker settings — env-driven, no secrets in the repo (``docs/01`` §1.4).

The Postgres DSN (which carries the password) and the service URLs come from the
environment; schedules have sane defaults but are overridable so the cadence can
be tuned per deployment without a code change. Mirrors the variables the sibling
services already read (``POSTGRES_DSN``, ``QDRANT_URL``, ``OLLAMA_HOST``,
``EMBED_MODEL``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/worker/worker/config.py -> repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Warehouse DSN. When unset the run store degrades to an in-memory store so
    # the scheduler still runs offline (with a warning).
    postgres_dsn: str | None = None
    # Schema + table the run records land in (the shared run-tracking contract).
    runs_schema: str = "insight"

    # Downstream services the jobs talk to. Passed through to ingestion /
    # retrieval; the worker itself does not open these connections.
    qdrant_url: str = "http://qdrant:6333"
    ollama_host: str = "http://ollama:11434"
    embed_model: str = "nomic-embed-text"

    # Health endpoint compose can probe.
    health_port: int = 8090

    # Schedule cadences (minutes / clock). Interval jobs are offset so the two
    # 30-minute jobs do not fire in the same tick and contend for the box.
    incremental_sync_minutes: int = 30
    reindex_docs_minutes: int = 30
    reindex_docs_offset_minutes: int = 15
    dbt_build_hour: int = 2
    dbt_build_minute: int = 0

    # dbt invocation (shelled out). Defaults point at the sibling warehouse
    # project so ``dbt build`` runs with the committed profile.
    dbt_project_dir: Path = _REPO_ROOT / "services" / "warehouse"
    dbt_profiles_dir: Path = _REPO_ROOT / "services" / "warehouse"
    dbt_executable: str = "dbt"
    dbt_timeout_seconds: int = 1800

    # What `reindex_docs` indexes.
    #   "ingested" (default) -> the corpus services/ingestion publishes, i.e.
    #                           the REAL documents (`document_corpus_path`);
    #   "samples"            -> the retrieval package's built-in demo set, an
    #                           EXPLICIT fallback, never a silent one;
    #   any other value      -> treated as a path to a folder / JSON file.
    reindex_source: str = "ingested"
    # The ingestion -> retrieval hand-off file. Must match
    # `IngestionSettings.document_corpus_path`.
    document_corpus_path: Path = _REPO_ROOT / "data" / "ingested" / "documents.json"
    # Re-embed only documents whose content hash changed. Embedding is the
    # expensive step, so the scheduled half-hourly reindex must not redo it all.
    reindex_changed_only: bool = True


def get_settings() -> WorkerSettings:
    return WorkerSettings()
