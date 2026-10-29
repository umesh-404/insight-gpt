"""Attachment processor for file-grounded Q&A.

Supports extracting clean text, tabular data, and schema insights from files
attached by users:
- Delimited data: CSV, TSV (with headers, column dtypes, basic stats, preview rows)
- Spreadsheets: Excel (.xlsx, .xls) via openpyxl
- Documents: PDF (.pdf) via pypdf and fallback stream extraction, DOCX (.docx) via zip/xml
- Structured: JSON (records arrays or key-value structures)
- Code & text: TXT, Markdown, SQL, Python, logs, YAML, HTML, etc.

Produces:
1. `RetrievedDoc` blocks that feed directly into RAG synthesis with `source_type="attachment"`
2. Structured `Table` objects that surface in `AnswerEnvelope.tables`
3. Neutralized evidence summaries with clean citations so answers cite `[1] Attached: filename`
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .envelope import Citation, Table
from .retrieval import RetrievedDoc

log = logging.getLogger("insightgpt.attachments")

# Maximum text length extracted from an attachment to prevent prompt overflow
MAX_ATTACHMENT_CHARS = 120_000
MAX_TABLE_PREVIEW_ROWS = 100


class AttachmentInput(BaseModel):
    """The wire shape of an attached file sent in an ask request."""

    name: str = Field(min_length=1, max_length=255)
    content: str = Field(description="Raw text or base64 data (including data: URLs)")
    content_type: str | None = None
    size: int | None = None


@dataclass
class ProcessedAttachment:
    """An attachment parsed into structured table, text, and summary evidence."""

    name: str
    content_type: str
    text_content: str
    table: Table | None = None
    summary: str = ""
    row_count: int = 0
    column_names: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_doc(self, today: str = "2026-07-15", index: int = 1) -> RetrievedDoc:
        body = self.text_content
        if len(body) > MAX_ATTACHMENT_CHARS:
            body = body[:MAX_ATTACHMENT_CHARS] + "\n... [content truncated for length]"
        return RetrievedDoc(
            doc_id=f"attachment:{self.name}",
            source_type="attachment",
            title=f"Attached file: {self.name}",
            body=body,
            date=today,
            score=1.0,
            metadata={
                "is_attachment": True,
                "filename": self.name,
                "row_count": self.row_count,
                "columns": self.column_names,
            },
        )

    def to_citation(self, today: str = "2026-07-15", n: int = 1) -> Citation:
        snippet = self.summary or self.text_content[:280]
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 280:
            snippet = snippet[:277] + "..."
        return Citation(
            n=n,
            doc_id=f"attachment:{self.name}",
            source_type="attachment",
            title=f"Attached: {self.name}",
            date=today,
            score=1.0,
            snippet=snippet,
            uri=None,
        )


def _decode_content(raw: str) -> tuple[str, bytes]:
    """Decode input content which may be a data URL, base64, or plain text string."""
    raw = raw.strip()
    if raw.startswith("data:") and ";base64," in raw:
        _, b64_payload = raw.split(";base64,", 1)
        try:
            raw_bytes = base64.b64decode(b64_payload)
            text = raw_bytes.decode("utf-8", errors="replace")
            return text, raw_bytes
        except Exception:
            return raw, raw.encode("utf-8", errors="replace")

    # If it's pure base64 without the data: prefix
    if len(raw) > 20 and len(raw) % 4 == 0 and re.match(r"^[A-Za-z0-9+/]+={0,2}$", raw):
        try:
            raw_bytes = base64.b64decode(raw)
            text = raw_bytes.decode("utf-8", errors="replace")
            return text, raw_bytes
        except Exception:
            pass

    return raw, raw.encode("utf-8", errors="replace")


def _infer_dtype(val: Any) -> str:
    if isinstance(val, (int, float)):
        return "number"
    if isinstance(val, bool):
        return "boolean"
    val_str = str(val).strip()
    if re.match(r"^-?\d+$", val_str):
        return "number"
    if re.match(r"^-?\d*\.\d+$", val_str):
        return "number"
    if re.match(r"^\$\s*-?\d+(?:,\d{3})*(?:\.\d+)?$", val_str):
        return "currency"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val_str):
        return "date"
    return "string"


def _clean_cell(val: Any) -> Any:
    if val is None:
        return ""
    if isinstance(val, (int, float, bool)):
        return val
    s = str(val).strip()
    # Try converting to int or float for table cells
    if re.match(r"^-?\d+$", s):
        try:
            return int(s)
        except ValueError:
            return s
    if re.match(r"^-?\d*\.\d+$", s):
        try:
            return float(s)
        except ValueError:
            return s
    return s


def _parse_csv_or_tsv(name: str, text: str, delimiter: str | None = None) -> ProcessedAttachment:
    """Parse CSV or TSV content into a Table, summaries, and markdown representation."""
    if delimiter is None:
        if name.lower().endswith(".tsv") or "\t" in text.split("\n", 1)[0]:
            delimiter = "\t"
        elif ";" in text.split("\n", 1)[0] and "," not in text.split("\n", 1)[0]:
            delimiter = ";"
        else:
            delimiter = ","

    f = io.StringIO(text)
    reader = csv.reader(f, delimiter=delimiter)
    all_rows: list[list[str]] = [row for row in reader if any(cell.strip() for cell in row)]

    if not all_rows:
        return ProcessedAttachment(
            name=name,
            content_type="text/csv",
            text_content="(Empty CSV file)",
            summary="Empty CSV file",
            row_count=0,
        )

    headers = [col.strip() or f"col_{i+1}" for i, col in enumerate(all_rows[0])]
    raw_data_rows = all_rows[1:]
    row_count = len(raw_data_rows)

    parsed_rows = [[_clean_cell(cell) for cell in r] for r in raw_data_rows]
    # Pad or truncate row values to match header length
    col_count = len(headers)
    normalized_rows = [
        (r[:col_count] + [""] * (col_count - len(r))) if len(r) < col_count else r[:col_count]
        for r in parsed_rows
    ]

    # Infer column data types for stats
    col_dtypes: dict[str, str] = {}
    for col_idx, col_name in enumerate(headers):
        sample_types = [
            _infer_dtype(r[col_idx])
            for r in normalized_rows[:20]
            if col_idx < len(r) and r[col_idx] != ""
        ]
        col_dtypes[col_name] = sample_types[0] if sample_types else "string"

    table = Table(
        title=f"Attached: {name}",
        columns=headers,
        rows=normalized_rows[:MAX_TABLE_PREVIEW_ROWS],
    )

    # Compute quick numeric statistics for synthesis
    numeric_stats: dict[str, dict[str, Any]] = {}
    for col_idx, col_name in enumerate(headers):
        if col_dtypes.get(col_name) in ("number", "currency"):
            nums = [
                float(r[col_idx])
                for r in normalized_rows
                if col_idx < len(r) and isinstance(r[col_idx], (int, float))
            ]
            if nums:
                numeric_stats[col_name] = {
                    "count": len(nums),
                    "sum": round(sum(nums), 2),
                    "avg": round(sum(nums) / len(nums), 2),
                    "min": round(min(nums), 2),
                    "max": round(max(nums), 2),
                }


    # Format markdown evidence representation
    lines = [
        f"File: {name} (CSV, {row_count} data rows, {col_count} columns)",
        f"Columns: {', '.join(headers)}",
    ]
    if numeric_stats:
        stats_str = "; ".join(
            f"{c}: total={s['sum']}, avg={s['avg']}, min={s['min']}, max={s['max']}"
            for c, s in numeric_stats.items()
        )
        lines.append(f"Summary Statistics: {stats_str}")

    lines.append("\nData Rows (first 100):")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in normalized_rows[:50]:
        lines.append("| " + " | ".join(str(cell) for cell in r) + " |")
    if row_count > 50:
        lines.append(f"... and {row_count - 50} more rows.")

    text_content = "\n".join(lines)
    summary = f"CSV with {row_count} rows across columns: {', '.join(headers[:6])}"

    return ProcessedAttachment(
        name=name,
        content_type="text/csv",
        text_content=text_content,
        table=table,
        summary=summary,
        row_count=row_count,
        column_names=headers,
        stats=numeric_stats,
    )


def _parse_excel(name: str, raw_bytes: bytes) -> ProcessedAttachment:
    """Parse Excel (.xlsx, .xls) workbook using openpyxl."""
    try:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        sheet = wb.active
        if sheet is None:
            return ProcessedAttachment(
                name=name,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                text_content="(Empty Excel file)",
                summary="Empty Excel file",
            )

        rows = list(sheet.iter_rows(values_only=True))
        all_rows = [[cell for cell in row] for row in rows if any(c is not None for c in row)]
        if not all_rows:
            return ProcessedAttachment(
                name=name,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                text_content="(Empty Excel sheet)",
                summary="Empty Excel sheet",
            )

        # Convert to CSV text representation and delegate to _parse_csv_or_tsv
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        for r in all_rows:
            writer.writerow([str(c) if c is not None else "" for c in r])
        proc = _parse_csv_or_tsv(name, csv_buffer.getvalue(), delimiter=",")
        proc.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        proc.summary = f"Excel spreadsheet with {proc.row_count} rows"
        return proc
    except Exception as exc:
        log.warning("Failed to parse Excel file %s: %s", name, exc)
        return ProcessedAttachment(
            name=name,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            text_content=f"(Could not parse Excel spreadsheet: {exc})",
            summary=f"Excel file ({name})",
        )


def _parse_pdf(name: str, raw_bytes: bytes) -> ProcessedAttachment:
    """Parse PDF document text using pypdf with fallback stream extraction."""
    extracted_pages: list[str] = []

    # 1. Primary: pypdf
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw_bytes))
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:
                extracted_pages.append(f"[Page {i + 1}]\n{text}")
    except Exception as exc:
        log.warning("pypdf extraction failed on %s: %s; trying fallback", name, exc)

    # 2. Fallback if pypdf extracted nothing or was unavailable
    if not extracted_pages:
        try:
            import zlib

            # Simple decompression of FlateDecode streams
            stream_regex = re.compile(b"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)
            decompressed: list[str] = []
            for m in stream_regex.finditer(raw_bytes):
                chunk = m.group(1)
                try:
                    data = zlib.decompress(chunk)
                    # Extract literal strings inside parentheses: (hello world) Tj
                    text_matches = re.findall(rb"\((.*?)\)\s*T[jJ]", data)
                    for tm in text_matches:
                        try:
                            s = tm.decode("utf-8", errors="ignore").strip()
                            if len(s) > 1 and s.isprintable():
                                decompressed.append(s)
                        except Exception:
                            pass
                except Exception:
                    pass
            if decompressed:
                extracted_pages.append(" ".join(decompressed))
        except Exception:
            pass

    if not extracted_pages:
        text_content = f"File: {name} (PDF document)\n(No extractable text found in this PDF.)"
        summary = f"PDF document: {name} (no extractable text)"
    else:
        pages_text = "\n\n".join(extracted_pages)
        text_content = f"File: {name} (PDF document, {len(extracted_pages)} pages)\n\n{pages_text}"
        first_page = extracted_pages[0] if extracted_pages else ""
        first_snip = first_page[:200].replace("\n", " ").strip()
        summary = f"PDF document ({len(extracted_pages)} pages): {first_snip}"


    return ProcessedAttachment(
        name=name,
        content_type="application/pdf",
        text_content=text_content,
        summary=summary,
        row_count=len(extracted_pages),
    )


def _parse_docx(name: str, raw_bytes: bytes) -> ProcessedAttachment:
    """Parse Word document (.docx) using built-in zipfile and XML parsing."""
    try:
        import xml.etree.ElementTree as ET
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if "word/document.xml" not in z.namelist():
                return ProcessedAttachment(
                    name=name,
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    text_content=f"File: {name} (Word document without document.xml)",
                    summary=f"Word document: {name}",
                )

            xml_data = z.read("word/document.xml")
            tree = ET.fromstring(xml_data)
            paragraphs: list[str] = []
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for p in tree.iter(f"{ns}p"):
                texts = [node.text for node in p.iter(f"{ns}t") if node.text]
                if texts:
                    paragraphs.append("".join(texts))

            text = "\n\n".join(paragraphs).strip()
            summary = (
                f"Word document: {text[:200].replace(chr(10), ' ')}"
                if text
                else f"Word document: {name}"
            )
            return ProcessedAttachment(
                name=name,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                text_content=f"File: {name} (Word Document)\n\n{text}",
                summary=summary,
                row_count=len(paragraphs),
            )
    except Exception as exc:
        log.warning("DOCX parsing failed on %s: %s", name, exc)
        return ProcessedAttachment(
            name=name,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            text_content=f"File: {name} (Word document - parse error: {exc})",
            summary=f"Word document: {name}",
        )


def _parse_json(name: str, text: str) -> ProcessedAttachment:
    """Parse JSON file: if list of dicts, format as Table; else structured text."""
    try:
        parsed = json.loads(text)
    except Exception as exc:
        return ProcessedAttachment(
            name=name,
            content_type="application/json",
            text_content=f"File: {name} (Malformed JSON: {exc})\n\n{text[:2000]}",
            summary=f"JSON file: {name}",
        )

    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed[:10]):
        # Tabular records!
        keys = list(dict.fromkeys(k for item in parsed for k in item))

        rows = [[_clean_cell(item.get(k, "")) for k in keys] for item in parsed]
        table = Table(
            title=f"Attached: {name}",
            columns=keys,
            rows=rows[:MAX_TABLE_PREVIEW_ROWS],
        )

        lines = [
            f"File: {name} (JSON array of {len(parsed)} objects)",
            f"Fields: {', '.join(keys)}",
            "\nPreview Records:",
            json.dumps(parsed[:10], indent=2),
        ]
        return ProcessedAttachment(
            name=name,
            content_type="application/json",
            text_content="\n".join(lines),
            table=table,
            summary=f"JSON records ({len(parsed)} items): fields {', '.join(keys[:5])}",
            row_count=len(parsed),
            column_names=keys,
        )

    # General dictionary or arbitrary JSON
    formatted = json.dumps(parsed, indent=2)
    if len(formatted) > MAX_ATTACHMENT_CHARS:
        formatted = formatted[:MAX_ATTACHMENT_CHARS] + "\n... [truncated]"
    return ProcessedAttachment(
        name=name,
        content_type="application/json",
        text_content=f"File: {name} (JSON)\n\n{formatted}",
        summary=f"JSON data ({name})",
    )


def process_attachment(att: AttachmentInput) -> ProcessedAttachment:
    """Read and parse an attachment based on its filename extension and content."""
    text, raw_bytes = _decode_content(att.content)
    lower_name = att.name.lower()

    if lower_name.endswith(".csv"):
        return _parse_csv_or_tsv(att.name, text, delimiter=",")
    if lower_name.endswith(".tsv"):
        return _parse_csv_or_tsv(att.name, text, delimiter="\t")
    if lower_name.endswith((".xlsx", ".xls")):
        return _parse_excel(att.name, raw_bytes)
    if lower_name.endswith(".pdf"):
        return _parse_pdf(att.name, raw_bytes)
    if lower_name.endswith(".docx"):
        return _parse_docx(att.name, raw_bytes)
    if lower_name.endswith(".json"):
        return _parse_json(att.name, text)

    # Plain text, markdown, logs, code, etc.
    summary = text[:200].replace("\n", " ").strip()
    return ProcessedAttachment(
        name=att.name,
        content_type=att.content_type or "text/plain",
        text_content=f"File: {att.name}\n\n{text}",
        summary=f"Attached file {att.name}: {summary}",
        row_count=len(text.splitlines()),
    )


def process_attachments(attachments: list[AttachmentInput] | None) -> list[ProcessedAttachment]:
    """Parse all attachments safely, logging any errors without failing the request."""
    if not attachments:
        return []
    results: list[ProcessedAttachment] = []
    for att in attachments:
        try:
            results.append(process_attachment(att))
        except Exception as exc:
            log.exception("Error processing attachment %s: %s", att.name, exc)
            results.append(
                ProcessedAttachment(
                    name=att.name,
                    content_type="application/octet-stream",
                    text_content=f"File: {att.name} (Error parsing file content: {exc})",
                    summary=f"File {att.name}",
                )
            )
    return results
