"""The answer envelope — the engine's single typed output.

Every field except ``answer`` is optional: a pure-structured answer carries
``sql`` + ``tables`` + ``chart`` and no ``citations``; a pure-unstructured answer
carries ``citations`` and no ``sql``. ``confidence`` and ``caveats`` are always
honest about what the answer rests on. Mirrors ``docs/05-insight-engine.md`` §6.1.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class Table(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[Any]]


class Citation(BaseModel):
    n: int
    doc_id: str
    source_type: str
    title: str
    date: str | None = None
    score: float | None = None


class ChartSeries(BaseModel):
    name: str
    y: str


class Chart(BaseModel):
    type: Literal["bar", "line", "area", "pie", "table"] = "bar"
    x: str | None = None
    series: list[ChartSeries] = Field(default_factory=list)
    data_ref: str = "tables[0]"


class AnswerEnvelope(BaseModel):
    answer: str
    route: Literal["structured", "unstructured", "hybrid", "clarify"] = "structured"
    sql: list[str] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    chart: Chart | None = None
    confidence: Confidence = "medium"
    caveats: list[str] = Field(default_factory=list)
    # Present when the router needs the user to disambiguate before answering.
    clarifying_question: str | None = None
