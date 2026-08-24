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


def get_settings() -> IngestionSettings:
    return IngestionSettings()
