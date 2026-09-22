"""Lazily-built server context: settings, catalog, engine — plus small helpers.

The engine is built **once, on first use**, not at import time. Two reasons:
``python -m insight_mcp --print-config`` must not need a warehouse, and an MCP
client that launches the server eagerly should not pay for DuckDB fixture
construction (or a Postgres connection) before it has sent a single request.

Backend selection is entirely delegated to ``app.engine.build.build_engine`` —
the same function the API uses — so the offline default (``WAREHOUSE=duckdb``,
``RETRIEVER=fixture``, ``LLM_PROVIDER=fake``) and the real Postgres/Qdrant
backends behave identically on both surfaces.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.config import Settings, get_settings
from app.engine.build import build_engine, select_retriever, select_warehouse
from app.engine.engine import InsightEngine
from app.engine.envelope import AnswerEnvelope
from app.semantic.catalog import Metric, SemanticCatalog, default_catalog_path, load_catalog

from .models import BackendStatus, LlmStatus

# Providers whose reachability is a local decision, not a network call.
_OFFLINE_PROVIDERS = {"fake"}
# Cloud providers and the env var carrying their key. Only *presence* is ever
# reported; the value is never read into a response.
_PROVIDER_KEY_ENV = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}

# Leading aggregate in a metric expression -> the ``default_agg`` vocabulary
# (display metadata; the authoritative aggregation is the catalog expression).
_AGG_PREFIX = (
    ("SUM(", "sum"),
    ("COUNT(", "count"),
    ("AVG(", "avg"),
    ("MIN(", "min"),
    ("MAX(", "max"),
)

_MAX_EXCERPT_CHARS = 700


@dataclass
class ServerContext:
    """One process-wide handle to the governed stack."""

    _settings: Settings | None = None
    _catalog: SemanticCatalog | None = None
    _engine: InsightEngine | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def catalog(self) -> SemanticCatalog:
        if self._catalog is None:
            self._catalog = load_catalog()
        return self._catalog

    @property
    def engine(self) -> InsightEngine:
        if self._engine is None:
            log(
                f"building engine (warehouse={select_warehouse(self.settings)}, "
                f"retriever={select_retriever(self.settings)}, "
                f"provider={self.settings.llm_provider})"
            )
            self._engine = build_engine(self.settings)
            # The engine builds its own catalog; share the same instance so the
            # catalog tools and the query path can never disagree.
            self._catalog = self._engine.catalog
        return self._engine

    @property
    def warehouse_mode(self) -> str:
        return select_warehouse(self.settings)

    @property
    def retriever_mode(self) -> str:
        return select_retriever(self.settings)


CONTEXT = ServerContext()


def log(message: str) -> None:
    """Diagnostics go to stderr.

    stdio transport means stdout carries JSON-RPC framing; a stray ``print`` to
    stdout corrupts the session for the client.
    """
    print(f"[insightgpt-mcp] {message}", file=sys.stderr, flush=True)


def catalog_source() -> str:
    """Where the governed layer is defined, for attribution in tool output."""
    try:
        return str(default_catalog_path())
    except Exception:  # noqa: BLE001 — attribution is nice-to-have, never fatal
        return "config/semantic_layer.yml"


def default_agg(metric: Metric) -> str:
    if not metric.additive:
        return "ratio"
    expr = metric.expr.strip().upper()
    for prefix, agg in _AGG_PREFIX:
        if expr.startswith(prefix):
            return agg
    return "sum"


def describe_metric(metric: Metric, unit: str) -> str:
    dims = ", ".join(sorted(metric.dimensions)) or "no dimensions"
    return f"{metric.label} ({unit}) over {metric.fact}; groupable by {dims}."


def label_for(key: str) -> str:
    return key.replace("_", " ").capitalize()


def describe_time_grains(catalog: SemanticCatalog) -> list[str]:
    return sorted({g for d in catalog.dimensions.values() if d.is_date() for g in d.grains})


def describe_summary(envelope: AnswerEnvelope) -> str:
    """Render an answer envelope as a short, text-only digest.

    Clients that show only the text block still need to see the *shape* of the
    answer — above all whether the engine abstained. Abstention leads, because
    a refusal buried under a paragraph of prose reads like an answer.
    """
    lines: list[str] = []
    if envelope.abstained:
        lines.append(f"ABSTAINED — {envelope.abstain_reason or 'cannot answer reliably'}")
        if envelope.suggestions:
            lines.append(f"Closest governed metrics: {', '.join(envelope.suggestions)}")
    elif envelope.clarifying_question:
        lines.append(f"NEEDS CLARIFICATION — {envelope.clarifying_question}")
    lines.append(envelope.answer)
    lines.append(f"route={envelope.route} confidence={envelope.confidence}")
    if envelope.tables:
        shapes = ", ".join(f"{t.title} ({len(t.rows)} rows)" for t in envelope.tables)
        lines.append(f"tables: {shapes}")
    if envelope.sql:
        lines.append(f"sql statements: {len(envelope.sql)} (full text in structured output)")
    if envelope.citations:
        lines.append(
            "citations: " + ", ".join(f"[{c.n}] {c.title}" for c in envelope.citations)
        )
    for caveat in envelope.caveats:
        lines.append(f"caveat: {caveat}")
    return "\n".join(lines)


def describe_status(ctx: ServerContext) -> tuple[BackendStatus, BackendStatus, LlmStatus]:
    """Probe the configured backends. A backend that is down is a report, not an error."""
    warehouse_mode = "duckdb-fixture" if ctx.warehouse_mode == "duckdb" else "postgres"
    try:
        table = ctx.catalog.allow_tables[0]
        ctx.engine.warehouse.run(f"SELECT COUNT(*) AS n FROM {table}", [])
        warehouse = BackendStatus(
            mode=warehouse_mode,
            reachable=True,
            detail="in-process DuckDB fixture"
            if warehouse_mode == "duckdb-fixture"
            else "read-only Postgres role",
        )
    except Exception as exc:  # noqa: BLE001
        warehouse = BackendStatus(
            mode=warehouse_mode, reachable=False, detail=short(f"{type(exc).__name__}: {exc}")
        )

    retriever_mode = ctx.retriever_mode
    try:
        ctx.engine.retriever.search("status probe", filters={}, k=1)
        retriever = BackendStatus(
            mode=retriever_mode,
            reachable=True,
            detail="in-process keyword retriever over sample documents"
            if retriever_mode == "fixture"
            else "Qdrant hybrid search",
        )
    except Exception as exc:  # noqa: BLE001
        retriever = BackendStatus(
            mode=retriever_mode, reachable=False, detail=short(f"{type(exc).__name__}: {exc}")
        )

    provider = str(getattr(ctx.engine.provider, "name", ctx.settings.llm_provider))
    raw_model = getattr(ctx.engine.provider, "model", ctx.settings.llm_model)
    key_env = _PROVIDER_KEY_ENV.get(provider)
    configured = bool(os.getenv(key_env)) if key_env else None
    llm = LlmStatus(
        provider=provider,
        model=str(raw_model) if raw_model else None,
        credential_configured=configured,
        detail=(
            "offline deterministic provider"
            if provider in _OFFLINE_PROVIDERS
            else "local provider" if provider == "ollama"
            else "credential configured" if configured
            else "no credential configured"
        ),
    )
    return warehouse, retriever, llm


def short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def dtype_of(rows: list[list[Any]], col: int) -> str:
    for row in rows:
        value = row[col]
        if value is None:
            continue
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float | Decimal):
            return "number"
        return "string"
    return "string"


def jsonable(value: Any) -> Any:
    """Coerce warehouse values into JSON-safe primitives.

    Rows arrive as whatever the driver produced — ``Decimal`` for Postgres
    numerics, ``date``/``datetime`` for date columns. MCP ``structuredContent``
    is JSON, so these are normalized once here rather than relying on
    best-effort serialization of an ``Any``-typed field.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    return str(value)


def excerpt(body: str, limit: int = _MAX_EXCERPT_CHARS) -> str:
    """Bound document bodies. An unclamped corpus dump floods the client's context."""
    text = " ".join(body.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
