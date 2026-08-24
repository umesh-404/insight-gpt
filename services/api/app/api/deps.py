"""Shared, cached dependencies: the catalog, the insight engine, app metadata.

The engine is built once and reused. It is config-driven (doc 06 §3.1): with the
default settings it uses the offline fixture stack (fake/Ollama provider + the
in-process DuckDB warehouse); pointing ``WAREHOUSE`` at a Postgres DSN later
swaps the executor without changing any router.
"""

from __future__ import annotations

import time
from functools import lru_cache

from ..config import Settings, get_settings
from ..engine.build import build_engine
from ..engine.engine import InsightEngine
from ..semantic.catalog import SemanticCatalog, load_catalog
from ..warehouse.executor import Warehouse

APP_VERSION = "0.1.0"
_STARTED_AT = time.time()


def uptime_seconds() -> float:
    return round(time.time() - _STARTED_AT, 3)


@lru_cache(maxsize=1)
def get_catalog() -> SemanticCatalog:
    return load_catalog()


@lru_cache(maxsize=1)
def _build_engine() -> InsightEngine:
    # Config-driven: offline fixture stack by default; real Postgres/Qdrant
    # backends when WAREHOUSE/RETRIEVER select them (see engine/build.py).
    return build_engine(get_settings())


def get_engine() -> InsightEngine:
    return _build_engine()


def get_warehouse() -> Warehouse:
    return get_engine().warehouse


def app_settings() -> Settings:
    return get_settings()


def reset_caches() -> None:
    """Drop cached singletons (used by tests that flip env before building)."""
    _build_engine.cache_clear()
    get_catalog.cache_clear()
