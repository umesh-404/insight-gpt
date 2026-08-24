"""Unit tests for the config-driven engine builder (engine/build.py).

Pure offline: the default fixture stack is built for real; the Postgres branch
is constructed (no connection is opened in ``__init__``); the Qdrant branch is
asserted via the decision function and a monkeypatched builder, so the optional
``retrieval`` package need not be installed.
"""

from __future__ import annotations

from app.config import Settings
from app.engine import build
from app.engine.retrieval import FixtureRetriever
from app.warehouse.executor import DuckDBWarehouse, PostgresWarehouse


def test_defaults_select_fixture_stack() -> None:
    s = Settings()
    assert build.select_warehouse(s) == "duckdb"
    assert build.select_retriever(s) == "fixture"

    engine = build.build_engine(s)
    assert isinstance(engine.warehouse, DuckDBWarehouse)
    assert isinstance(engine.retriever, FixtureRetriever)


def test_postgres_selected_only_with_dsn() -> None:
    # Selector alone without a DSN stays on the safe offline default.
    assert build.select_warehouse(Settings(warehouse="postgres")) == "duckdb"

    s = Settings(warehouse="postgres", postgres_dsn="postgresql://ro@db/marts")
    assert build.select_warehouse(s) == "postgres"

    engine = build.build_engine(s)
    assert isinstance(engine.warehouse, PostgresWarehouse)
    # Retriever still the offline default unless RETRIEVER=qdrant.
    assert isinstance(engine.retriever, FixtureRetriever)


def test_qdrant_branch_selected_without_live_client(monkeypatch) -> None:
    s = Settings(retriever="qdrant")
    assert build.select_retriever(s) == "qdrant"

    sentinel = object()
    monkeypatch.setattr(build, "_build_qdrant_retriever", lambda: sentinel)
    engine = build.build_engine(s)
    assert engine.retriever is sentinel
    # Warehouse untouched by the retriever selector.
    assert isinstance(engine.warehouse, DuckDBWarehouse)
