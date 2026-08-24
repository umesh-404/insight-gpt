"""Runtime settings — env-driven, no secrets in the repo.

Secrets (cloud LLM keys, DB passwords) come from the environment; this only
holds non-secret selectors and hosts. Mirrors the variables documented in
``docs/09-deployment.md``.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # LLM provider selection (secrets are read by the provider factory from env).
    llm_provider: str = "ollama"
    llm_model: str | None = None
    ollama_host: str = "http://127.0.0.1:11434"

    # Warehouse: "duckdb" fixture (default, offline) or a Postgres DSN.
    warehouse: str = "duckdb"
    postgres_dsn: str | None = None

    # Reference "as of" date used to resolve relative time ranges.
    today: str = "2026-07-15"


def get_settings() -> Settings:
    return Settings()
