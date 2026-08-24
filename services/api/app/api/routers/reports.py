"""Executive reports (analyst+) — doc 06 §3.5.

``POST /reports`` runs a set of governed questions through the insight engine
and assembles a cited narrative; ``GET /reports/{id}`` returns it; ``GET
/reports/{id}/export`` streams it as Markdown or as a **server-rendered PDF**
(``application/pdf`` + ``Content-Disposition: attachment``). Both exports render
from the same stored blocks, so the PDF matches the on-screen preview: title,
period, generation timestamp, then every section's narrative, its key figures
table, and its citations.

Generation is fast on the offline stack, so it runs inline and the report is
stored ``ready`` (or ``failed`` with the reason recorded).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...auth.roles import Role, require_role
from ...engine.engine import InsightEngine
from ...engine.envelope import Chart, Citation, Table
from ..deps import get_engine, rate_limit
from ..errors import NotFoundError

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

_MAX_REPORTS = 200          # keep the in-process store bounded
_PDF_TABLE_ROWS = 12        # rows of key figures rendered per section


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
    # The figures behind the prose, so the export is not narrative-only.
    tables: list[Table] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class Report(BaseModel):
    id: str
    status: Literal["generating", "ready", "failed"]
    title: str
    period: TimeRange
    blocks: list[ReportBlock] = Field(default_factory=list)
    created_at: datetime
    error: str | None = None


class ReportHandle(BaseModel):
    report_id: str
    status: str
    href: str


_REPORTS: dict[str, Report] = {}


def reset_state() -> None:
    """Clear the stored reports (tests)."""
    _REPORTS.clear()


def _trim() -> None:
    if len(_REPORTS) <= _MAX_REPORTS:
        return
    for rid in sorted(_REPORTS, key=lambda r: _REPORTS[r].created_at)[
        : len(_REPORTS) - _MAX_REPORTS
    ]:
        _REPORTS.pop(rid, None)


def _build_blocks_sync(engine: InsightEngine, sections: list[str]) -> list[ReportBlock]:
    """Run every section's question. Synchronous — call via run_in_threadpool."""
    blocks: list[ReportBlock] = []
    for section in sections:
        heading, question = _SECTION_QUESTIONS[section]
        env = engine.ask(question)
        blocks.append(
            ReportBlock(
                heading=heading,
                prose=env.answer,
                chart_spec=env.chart,
                tables=list(env.tables),
                citations=list(env.citations),
            )
        )
    return blocks


def _require_report(report_id: str) -> Report:
    report = _REPORTS.get(report_id)
    if report is None:
        raise NotFoundError(f"No report with id {report_id!r}.")
    return report


@router.post(
    "/reports",
    response_model=ReportHandle,
    status_code=202,
    dependencies=[Depends(rate_limit("ask"))],
)
async def create_report(
    body: ReportRequest,
    _: object = Depends(require_role(Role.analyst)),
    engine: InsightEngine = Depends(get_engine),
) -> ReportHandle:
    rid = f"rep_{uuid.uuid4().hex[:12]}"
    error: str | None = None
    try:
        # One hop to the pool for the whole set, rather than one per section.
        blocks = await run_in_threadpool(_build_blocks_sync, engine, list(body.sections))
        status = "ready"
    except Exception as exc:  # noqa: BLE001 — a failed report is a recorded state
        blocks = []
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"[:500]
    report = Report(
        id=rid, status=status, title=body.title, period=body.period,
        blocks=blocks, created_at=datetime.now(UTC), error=error,
    )
    _REPORTS[rid] = report
    _trim()
    return ReportHandle(report_id=rid, status=status, href=f"/api/v1/reports/{rid}")


@router.get("/reports", response_model=list[Report])
async def list_reports(
    _: object = Depends(require_role(Role.analyst)),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Report]:
    ordered = sorted(_REPORTS.values(), key=lambda r: r.created_at, reverse=True)
    return ordered[:limit]


@router.get("/reports/{report_id}", response_model=Report)
async def get_report(
    report_id: str, _: object = Depends(require_role(Role.analyst))
) -> Report:
    return _require_report(report_id)


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: str,
    _: object = Depends(require_role(Role.analyst)),
    format: Literal["markdown", "pdf"] = Query(default="markdown"),
) -> Response:
    report = _require_report(report_id)
    if format == "pdf":
        pdf = await run_in_threadpool(render_pdf, report)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{report.id}.pdf"',
                "Content-Length": str(len(pdf)),
            },
        )
    markdown = _to_markdown(report)
    return PlainTextResponse(
        markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{report.id}.md"',
        },
    )


# --- rendering ----------------------------------------------------------------
def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        return f"{float(value):,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _to_markdown(report: Report) -> str:
    lines = [f"# {report.title}", ""]
    lines.append(f"_Period: {report.period.start} to {report.period.end}_")
    lines.append(f"_Generated: {report.created_at.isoformat(timespec='seconds')}_")
    lines.append("")
    if report.status == "failed":
        lines.append(f"> Report generation failed: {report.error or 'unknown error'}")
        lines.append("")
    for block in report.blocks:
        lines.append(f"## {block.heading}")
        lines.append("")
        lines.append(block.prose)
        lines.append("")
        for table in block.tables:
            lines.append(f"**{table.title}**")
            lines.append("")
            lines.append("| " + " | ".join(table.columns) + " |")
            lines.append("| " + " | ".join("---" for _ in table.columns) + " |")
            for row in table.rows[:_PDF_TABLE_ROWS]:
                lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
            lines.append("")
        if block.citations:
            lines.append("**Sources**")
            for c in block.citations:
                date = f" ({c.date})" if c.date else ""
                lines.append(f"- [{c.n}] {c.title}{date} — {c.source_type}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# The built-in PDF fonts are latin-1; map the typography we actually emit.
_TRANSLATE = str.maketrans(
    {
        "—": "-", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...", "•": "-",
        " ": " ", "−": "-", "€": "EUR", "→": "->",
    }
)


def _pdf_text(value: str) -> str:
    """Make text safe for the core latin-1 fonts without dropping meaning."""
    text = value.translate(_TRANSLATE)
    return text.encode("latin-1", "replace").decode("latin-1")


def _para(pdf: Any, width: float, height: float, text: str) -> None:
    """A wrapped paragraph that always returns the cursor to the left margin."""
    pdf.multi_cell(width, height, _pdf_text(text), new_x="LMARGIN", new_y="NEXT")


def render_pdf(report: Report) -> bytes:
    """Render the stored report blocks to a real PDF document."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(_pdf_text(report.title))
    pdf.set_creator("InsightGPT")
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 20)
    _para(pdf, width, 9, report.title)
    pdf.ln(1)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    _para(pdf, width, 5, f"Period: {report.period.start} to {report.period.end}")
    _para(
        pdf, width, 5,
        "Generated: "
        f"{report.created_at.strftime('%Y-%m-%d %H:%M UTC')}  |  "
        f"Report {report.id}  |  Status {report.status}",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    if report.status == "failed":
        pdf.set_font("Helvetica", "B", 11)
        _para(pdf, width, 6, f"Report generation failed: {report.error or 'unknown error'}")
        return _as_bytes(pdf)

    for block in report.blocks:
        _pdf_block(pdf, block, width)

    return _as_bytes(pdf)


def _pdf_block(pdf: Any, block: ReportBlock, width: float) -> None:
    pdf.set_font("Helvetica", "B", 14)
    _para(pdf, width, 7, block.heading)
    pdf.ln(0.5)

    pdf.set_font("Helvetica", "", 11)
    _para(pdf, width, 5.5, block.prose or "(no narrative)")
    pdf.ln(2)

    for table in block.tables:
        _pdf_table(pdf, table, width)

    if block.citations:
        pdf.set_font("Helvetica", "B", 10)
        _para(pdf, width, 5, "Sources")
        pdf.set_font("Helvetica", "", 9)
        for c in block.citations:
            date = f" ({c.date})" if c.date else ""
            _para(pdf, width, 4.5, f"[{c.n}] {c.title}{date} - {c.source_type}")
    pdf.ln(4)


def _pdf_table(pdf: Any, table: Table, width: float) -> None:
    columns = list(table.columns)
    if not columns:
        return
    pdf.set_font("Helvetica", "B", 10)
    _para(pdf, width, 5, table.title or "Key figures")

    col_w = width / len(columns)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(238, 238, 238)
    for name in columns:
        pdf.cell(col_w, 6, _pdf_text(str(name))[:24], border=1, fill=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "", 9)
    for row in table.rows[:_PDF_TABLE_ROWS]:
        cells = list(row) + [None] * (len(columns) - len(row))
        for value in cells[: len(columns)]:
            pdf.cell(col_w, 5.5, _pdf_text(_cell(value))[:24], border=1)
        pdf.ln(5.5)
    if len(table.rows) > _PDF_TABLE_ROWS:
        pdf.set_font("Helvetica", "I", 8)
        _para(pdf, width, 4.5, f"... {len(table.rows) - _PDF_TABLE_ROWS} more row(s) omitted")
    pdf.ln(2)


def _as_bytes(pdf: Any) -> bytes:
    out = pdf.output()
    return bytes(out) if not isinstance(out, bytes) else out
