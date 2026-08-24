"""Executive reports (analyst+) — doc 06 §3.5.

``POST /reports`` runs a set of questions through the insight engine and
assembles a cited narrative; ``GET /reports/{id}`` returns it; ``GET
/reports/{id}/export`` streams the assembled Markdown. Generation is fast on the
offline stack, so it runs inline and the report is stored ``ready``. PDF export
is noted as a later addition (the doc specifies server-side PDF; Markdown is the
current, faithful source for it).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...engine.engine import InsightEngine
from ...engine.envelope import Chart, Citation
from ..deps import get_engine
from ..errors import BadRequestError, NotFoundError

router = APIRouter(tags=["reports"])

Section = Literal["kpis", "sales", "inventory", "voice_of_customer"]

# Each report section maps to a governed question the engine can answer.
_SECTION_QUESTIONS: dict[str, tuple[str, str]] = {
    "kpis": ("Key metrics", "What was revenue last quarter?"),
    "sales": ("Sales analysis", "Why did sales decline last quarter?"),
    "inventory": ("Inventory", "Which products should we restock?"),
    "voice_of_customer": (
        "Voice of customer",
        "Summarize customer complaints this month.",
    ),
}


class TimeRange(BaseModel):
    grain: str | None = None
    start: str
    end: str


class ReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    period: TimeRange
    sections: list[Section] = Field(min_length=1)
    format_hint: Literal["executive", "detailed"] = "executive"


class ReportBlock(BaseModel):
    heading: str
    prose: str
    chart_spec: Chart | None = None
    citations: list[Citation] = Field(default_factory=list)


class Report(BaseModel):
    id: str
    status: Literal["generating", "ready", "failed"]
    title: str
    period: TimeRange
    blocks: list[ReportBlock] = Field(default_factory=list)
    created_at: datetime


class ReportHandle(BaseModel):
    report_id: str
    status: str


_REPORTS: dict[str, Report] = {}


async def _build_blocks(engine: InsightEngine, sections: list[str]) -> list[ReportBlock]:
    blocks: list[ReportBlock] = []
    for section in sections:
        heading, question = _SECTION_QUESTIONS[section]
        env = await run_in_threadpool(engine.ask, question)
        blocks.append(
            ReportBlock(
                heading=heading,
                prose=env.answer,
                chart_spec=env.chart,
                citations=env.citations,
            )
        )
    return blocks


@router.post("/reports", response_model=ReportHandle, status_code=202)
async def create_report(
    body: ReportRequest,
    _: object = Depends(require_role(Role.analyst)),
    engine: InsightEngine = Depends(get_engine),
) -> ReportHandle:
    rid = f"rep_{uuid.uuid4().hex[:12]}"
    try:
        blocks = await _build_blocks(engine, body.sections)
        status = "ready"
    except Exception:  # noqa: BLE001 — a failed report is a valid, recorded state
        blocks = []
        status = "failed"
    report = Report(
        id=rid, status=status, title=body.title, period=body.period,
        blocks=blocks, created_at=datetime.now(UTC),
    )
    _REPORTS[rid] = report
    return ReportHandle(report_id=rid, status=status)


@router.get("/reports/{report_id}", response_model=Report)
async def get_report(
    report_id: str, _: object = Depends(require_role(Role.analyst))
) -> Report:
    report = _REPORTS.get(report_id)
    if report is None:
        raise NotFoundError(f"No report with id {report_id!r}.")
    return report


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: str,
    _: object = Depends(require_role(Role.analyst)),
    format: Literal["markdown", "pdf"] = Query(default="markdown"),
) -> PlainTextResponse:
    report = _REPORTS.get(report_id)
    if report is None:
        raise NotFoundError(f"No report with id {report_id!r}.")
    if format == "pdf":
        # PDF is produced server-side in a later phase (doc 06 §3.5); Markdown is
        # the faithful source it will render from.
        raise BadRequestError("PDF export is not yet available; use format=markdown.")
    markdown = _to_markdown(report)
    return PlainTextResponse(
        markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{report.id}.md"',
        },
    )


def _to_markdown(report: Report) -> str:
    lines = [f"# {report.title}", ""]
    lines.append(f"_Period: {report.period.start} to {report.period.end}_")
    lines.append("")
    for block in report.blocks:
        lines.append(f"## {block.heading}")
        lines.append("")
        lines.append(block.prose)
        lines.append("")
        if block.citations:
            lines.append("**Sources**")
            for c in block.citations:
                date = f" ({c.date})" if c.date else ""
                lines.append(f"- [{c.n}] {c.title}{date} — {c.source_type}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
