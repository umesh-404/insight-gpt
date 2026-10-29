"""Runtime settings — env-driven, no secrets in the repo.

Secrets (cloud LLM keys, DB passwords) come from the environment; this only
holds non-secret selectors and hosts. Mirrors the variables documented in
``docs/09-deployment.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_root_env() -> None:
    candidates = [
        Path(".env"),
        Path(__file__).resolve().parents[3] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass
            break


_load_root_env()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=(".env", str(Path(__file__).resolve().parents[3] / ".env")),
        extra="ignore",
    )

    # LLM provider selection (secrets are read by the provider factory from env).
    llm_provider: str = "ollama"
    llm_model: str | None = None
    ollama_host: str = "http://127.0.0.1:11434"
    typesafe_api_key: str | None = None
    typesafe_model: str = "jev-latest"

    # Warehouse: "duckdb" fixture (default, offline) or "postgres" (needs a DSN).
    warehouse: str = "duckdb"
    postgres_dsn: str | None = None

    # Retriever: "fixture" keyword scoring (default, offline) or "qdrant" (real).
    # The Qdrant client reads QDRANT_URL / OLLAMA_HOST / EMBED_MODEL from the
    # environment via the retrieval package's own config.
    retriever: str = "fixture"

    # Reference "as of" date used to resolve relative time ranges.
    today: str = "2026-07-15"


def get_settings() -> Settings:
    return Settings()

