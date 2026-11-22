"""Config-driven construction of the :class:`InsightEngine`.

Selects the warehouse and retriever backends from :class:`Settings`, defaulting
to the fully offline fixture stack (DuckDB + ``FixtureRetriever``). The real
backends are wired only when explicitly selected:

* ``WAREHOUSE=postgres`` (+ ``POSTGRES_DSN``) -> :class:`PostgresWarehouse`
* ``RETRIEVER=qdrant`` -> ``QdrantRetriever`` from the sibling ``retrieval``
  package, imported **lazily** so the offline API image and its tests never need
  it installed. See ``docs/05-insight-engine.md`` §7.

The two ``select_*`` functions are pure decisions (env -> backend name), so tests
can assert which branch is taken without instantiating a live client.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from ..engine.engine import InsightEngine
from ..engine.retrieval import FixtureRetriever, Retriever
from ..providers.factory import get_provider
from ..semantic.catalog import SemanticCatalog, load_catalog
from ..warehouse.executor import DuckDBWarehouse, PostgresWarehouse, Warehouse


def select_warehouse(settings: Settings) -> str:
    """Pure decision: which warehouse backend the settings select."""
    if settings.warehouse == "postgres" and settings.postgres_dsn:
        return "postgres"
    return "duckdb"


def select_retriever(settings: Settings) -> str:
    """Pure decision: which retriever backend the settings select."""
    if settings.retriever == "qdrant":
        return "qdrant"
    return "fixture"


def datasets_are_searchable(settings: Settings | None = None) -> bool:
    """Whether an uploaded dataset can actually be RETRIEVED by this deployment.

    Uploading indexes into Qdrant, but only the Qdrant retriever reads Qdrant.
    Under the default ``RETRIEVER=fixture`` an upload would be indexed and then
    never searched, while ``/ask`` answered the user's question about *their*
    file out of the retail sample corpus — an answer that looks right and is
    about someone else's data. So the upload path asks this first and declines
    honestly rather than accepting a file it cannot use.

    A pure decision from settings, like ``select_retriever``, so a caller can
    check it without constructing a client or reaching the network.
    """
    return select_retriever(settings or get_settings()) == "qdrant"


def _build_warehouse(kind: str, settings: Settings, catalog: SemanticCatalog) -> Warehouse:
    if kind == "postgres":
        return PostgresWarehouse(
            dsn=settings.postgres_dsn,  # type: ignore[arg-type]  # guarded by select_warehouse
            allow_tables=set(catalog.allow_tables),
            statement_timeout_ms=catalog.statement_timeout_ms,
        )
    return DuckDBWarehouse(allow_tables=set(catalog.allow_tables))


def _build_retriever(kind: str) -> Retriever:
    if kind == "qdrant":
        return _build_qdrant_retriever()
    return FixtureRetriever()


def _build_qdrant_retriever() -> Retriever:
    """Lazy-build the real Qdrant retriever.

    The ``retrieval`` package is an optional extra (absent from the offline
    default image and its tests), so it is imported only on this branch. Its
    config reads ``QDRANT_URL`` / ``QDRANT_COLLECTION`` / ``OLLAMA_HOST`` /
    ``EMBED_MODEL`` from the environment — the single source of truth for those
    endpoints, shared with the indexer so the two cannot point at different
    collections.
    """
    try:
        from retrieval.config import load_config
        from retrieval.retriever import QdrantRetriever
    except ImportError as exc:  # pragma: no cover - deployment error, not logic
        # Without this the symptom is a bare ModuleNotFoundError from inside a
        # request, which reads like a bug rather than a missing extra.
        raise RuntimeError(
            "RETRIEVER=qdrant needs the optional retrieval backend. "
            "Install it with `uv sync --extra real` (it is already in the "
            "container image), or set RETRIEVER=fixture to run offline."
        ) from exc

    return QdrantRetriever(config=load_config())


def build_engine(settings: Settings | None = None) -> InsightEngine:
    """Construct the engine from settings (default: the offline fixture stack)."""
    settings = settings or get_settings()
    provider = get_provider(settings.llm_provider, settings.llm_model)

    wkind = select_warehouse(settings)
    rkind = select_retriever(settings)

    # Fast path: both defaults -> the canonical fixture stack, unchanged.
    if wkind == "duckdb" and rkind == "fixture":
        return InsightEngine.fixture(provider=provider, today=settings.today)

    catalog = load_catalog()
    return InsightEngine(
        catalog=catalog,
        warehouse=_build_warehouse(wkind, settings, catalog),
        retriever=_build_retriever(rkind),
        provider=provider,
        today=settings.today,
    )
