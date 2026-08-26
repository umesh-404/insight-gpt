"""Typed tool outputs — the wire contract of the MCP server.

Every tool declares one of these as its return annotation, so the SDK publishes
a JSON ``outputSchema`` alongside each tool and returns validated
``structuredContent``. That is deliberate: this server's whole proposition is
that the *shape* of what a client may ask for and receive is governed and
knowable in advance, so the shape is advertised rather than implied.

The answer envelope is **inherited**, not re-declared
(:class:`AskResult` extends ``app.engine.envelope.AnswerEnvelope``), so the MCP
surface cannot drift from the API surface when the engine gains a field.
"""

from __future__ import annotations

from typing import Any, Literal

from app.engine.envelope import AnswerEnvelope
from pydantic import BaseModel, Field

# Catalog ``format`` -> the unit vocabulary shared with the REST catalog
# endpoint. Display vocabulary only; it carries no governance meaning.
UNIT_BY_FORMAT = {
    "currency": "currency",
    "integer": "count",
    "number": "count",
    "percent": "ratio",
    "duration": "duration",
}


class MetricInfo(BaseModel):
    """One governed metric, as the client is allowed to see it."""

    key: str
    label: str
    description: str
    format: str
    unit: str
    additive: bool = True
    default_agg: str
    aliases: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    fact: str


class DimensionInfo(BaseModel):
    """One governed dimension. ``grains`` is non-empty only for date dimensions."""

    key: str
    label: str
    table: str
    is_date: bool = False
    grains: list[str] = Field(default_factory=list)
    default_grain: str | None = None
    expression: str | None = None


class CatalogLimits(BaseModel):
    max_rows: int
    default_rows: int
    statement_timeout_ms: int


class MetricCatalog(BaseModel):
    version: int
    metrics: list[MetricInfo]
    time_grains: list[str] = Field(default_factory=list)
    allow_tables: list[str] = Field(default_factory=list)
    limits: CatalogLimits
    note: str


class DimensionCatalog(BaseModel):
    version: int
    dimensions: list[DimensionInfo]
    time_grains: list[str] = Field(default_factory=list)
    note: str


class ColumnSpec(BaseModel):
    name: str
    dtype: str
    role: Literal["dimension", "metric"] = "dimension"


class MetricQueryResult(BaseModel):
    """A governed query result plus the exact SQL that produced it."""

    metric: str
    label: str
    format: str
    unit: str
    dimensions: list[str] = Field(default_factory=list)
    time_grain: str | None = None
    order: Literal["asc", "desc"] | None = None
    limit: int
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int
    truncated: bool
    # The SQL is emitted with ``?`` placeholders and bound parameters; both are
    # returned so a reader can reproduce exactly what ran.
    sql: str
    params: list[str | int | float] = Field(default_factory=list)


class AskResult(AnswerEnvelope):
    """The engine's answer envelope, with a rendered one-screen summary in front.

    ``summary`` is a courtesy for text-only rendering; the authoritative values
    are the envelope fields (``sql``, ``tables``, ``citations``, ``abstained``…)
    carried through unchanged from the engine.
    """

    summary: str = ""


class DocumentHit(BaseModel):
    n: int
    doc_id: str
    source_type: str
    title: str
    date: str | None = None
    score: float | None = None
    excerpt: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSearchResult(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    retriever: str
    result_count: int
    results: list[DocumentHit] = Field(default_factory=list)


class MetricExplanation(BaseModel):
    """Everything needed to defend a number: definition, source, and limits."""

    key: str
    label: str
    definition: str
    expression: str
    fact: str
    fact_grain: str
    additive: bool
    additivity_note: str
    format: str
    unit: str
    default_agg: str
    aliases: list[str] = Field(default_factory=list)
    allowed_dimensions: list[DimensionInfo] = Field(default_factory=list)
    time_grains: list[str] = Field(default_factory=list)
    tables_touched: list[str] = Field(default_factory=list)
    source: str


class BackendStatus(BaseModel):
    mode: str
    reachable: bool
    detail: str | None = None


class LlmStatus(BaseModel):
    provider: str
    model: str | None = None
    # Whether a credential is configured. The credential itself is never read
    # into a response.
    credential_configured: bool | None = None
    detail: str | None = None


class CatalogStats(BaseModel):
    version: int
    metrics: int
    dimensions: int
    facts: int
    allow_tables: int


class SystemStatus(BaseModel):
    server: str
    version: str
    status: Literal["ok", "degraded"]
    as_of_date: str
    warehouse: BackendStatus
    retriever: BackendStatus
    llm: LlmStatus
    catalog: CatalogStats
    tools: list[str] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
