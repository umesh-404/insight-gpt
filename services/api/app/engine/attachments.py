"""Attachment processing engine for uploaded spreadsheets and documents.

Processes real uploaded Excel files (.xlsx, .xls), CSVs, PDFs, Word docs (.docx),
and text files. Queries spreadsheets using in-memory DuckDB SQL and extracts
fact-grounded answers and citations directly from document content, eliminating
hallucinated or canned synthetic answers.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
from pydantic import BaseModel

from .envelope import AnswerEnvelope, Chart, ChartSeries, Citation, Table


@dataclass
class ParsedSheet:
    sheet_name: str
    table_name: str
    columns: list[str]
    rows: list[list[Any]]
    types: list[str]  # 'DOUBLE', 'VARCHAR', 'BIGINT', etc.
    row_count: int
    numeric_summaries: dict[str, dict[str, float]] = field(default_factory=dict)
    banners: list[str] = field(default_factory=list)


@dataclass
class ParsedSpreadsheet:
    filename: str
    sheets: list[ParsedSheet]
    duckdb_conn: Any = None


@dataclass
class ParsedDocSection:
    section_index: int
    title: str
    page: int | None
    text: str


@dataclass
class ParsedDocument:
    filename: str
    full_text: str
    sections: list[ParsedDocSection]
    metadata: dict[str, Any] = field(default_factory=dict)


def sanitize_sql_ident(ident: str) -> str:
    """Sanitize column or table name for DuckDB SQL."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", ident.strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"col_{cleaned}"
    return cleaned.lower()


def infer_duckdb_type(values: list[Any]) -> str:
    """Infer DuckDB column type from sample values."""
    non_null = [v for v in values if v is not None and v != ""]
    if not non_null:
        return "VARCHAR"
    is_int = True
    is_float = True
    for v in non_null:
        if isinstance(v, bool):
            return "BOOLEAN"
        if isinstance(v, int) and not isinstance(v, bool):
            continue
        if isinstance(v, (float, Decimal)):
            is_int = False
            continue
        if isinstance(v, str):
            clean_str = v.replace(",", "").replace("$", "").replace("₹", "").replace("€", "").strip()
            try:
                int(clean_str)
                continue
            except ValueError:
                is_int = False
            try:
                float(clean_str)
                continue
            except ValueError:
                is_float = False
                return "VARCHAR"
        else:
            return "VARCHAR"
    if is_int:
        return "BIGINT"
    if is_float:
        return "DOUBLE"
    return "VARCHAR"


def detect_header_and_columns(
    raw_rows: list[list[Any] | tuple[Any, ...]],
) -> tuple[int, list[int], list[str], list[str]]:
    """Detect header row index, active column indices, clean headers, and banner metadata.

    Handles real-world spreadsheets with:
    - Empty columns on the left (e.g. columns 1..13 blank)
    - Metadata/banner titles at the top (company name, address, title)
    - Sub-headers and summary footers
    """
    if not raw_rows:
        return 0, [], [], []

    header_keywords = {
        "no", "sno", "s_no", "s.no", "id", "code", "sku", "name", "desc", "description",
        "item", "product", "particulars", "qty", "quantity", "case", "weight", "wht",
        "gms", "kg", "unit", "price", "rate", "cost", "mrp", "invoice", "margin",
        "amount", "total", "tax", "gst", "cgst", "sgst", "discount", "retailer",
        "distributor", "customer", "vendor", "date", "month", "year", "category",
        "status", "type", "sales", "revenue", "profit", "balance", "credit", "debit"
    }

    max_cols = max((len(r) for r in raw_rows), default=0)
    if max_cols == 0:
        return 0, [], [], []

    # Check which column indices have any non-empty data across the sheet
    col_has_data = [False] * max_cols
    for r in raw_rows:
        for c, val in enumerate(r):
            if val is not None and str(val).strip() != "":
                col_has_data[c] = True

    active_cols = [c for c, has in enumerate(col_has_data) if has]
    if not active_cols:
        return 0, [], [], []

    best_score = -9999
    best_row_idx = 0

    candidate_rows = range(min(30, len(raw_rows)))
    for r_idx in candidate_rows:
        row = raw_rows[r_idx]
        non_empty_cells = []
        for c in active_cols:
            if c < len(row) and row[c] is not None and str(row[c]).strip() != "":
                non_empty_cells.append((c, str(row[c]).strip()))

        count = len(non_empty_cells)
        if count == 0:
            continue

        if count == 1:
            score = -50
        else:
            score = count * 3
            # Check keywords and text types
            for _, text in non_empty_cells:
                tokens = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))
                matched = tokens.intersection(header_keywords)
                if matched:
                    score += 15 * len(matched)
                try:
                    float(text.replace(",", "").replace("$", "").replace("₹", ""))
                    score -= 5
                except ValueError:
                    score += 4

            # Bonus if row+1 exists and has aligned data
            if r_idx + 1 < len(raw_rows):
                next_row = raw_rows[r_idx + 1]
                next_count = sum(
                    1 for c in active_cols
                    if c < len(next_row) and next_row[c] is not None and str(next_row[c]).strip() != ""
                )
                if next_count >= count * 0.7:
                    score += 20

        if score > best_score:
            best_score = score
            best_row_idx = r_idx

    # Banner metadata rows before header
    banners: list[str] = []
    for r_idx in range(best_row_idx):
        row = raw_rows[r_idx]
        items = [
            str(row[c]).strip()
            for c in active_cols
            if c < len(row) and row[c] is not None and str(row[c]).strip() != ""
        ]
        if items:
            banners.append(" - ".join(items))

    # In header row, check active cols
    header_row = raw_rows[best_row_idx]
    final_active_cols: list[int] = []
    raw_headers: list[str] = []
    for c in active_cols:
        val = header_row[c] if c < len(header_row) else None
        h_str = str(val).strip() if val is not None and str(val).strip() != "" else ""
        has_subsequent_data = any(
            c < len(r) and r[c] is not None and str(r[c]).strip() != ""
            for r in raw_rows[best_row_idx + 1 :]
        )
        if h_str or has_subsequent_data:
            final_active_cols.append(c)
            raw_headers.append(h_str)

    # Deduplicate headers and assign fallback
    seen: dict[str, int] = {}
    unique_headers: list[str] = []
    for i, h in enumerate(raw_headers):
        base = h if h else f"Column_{i + 1}"
        if base in seen:
            seen[base] += 1
            unique_headers.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 0
            unique_headers.append(base)

    return best_row_idx, final_active_cols, unique_headers, banners


def parse_excel_bytes(payload: bytes, filename: str) -> ParsedSpreadsheet:
    """Parse an Excel (.xlsx, .xls) workbook into structured sheets and DuckDB tables."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(payload), data_only=True)
    con = duckdb.connect(":memory:")
    parsed_sheets: list[ParsedSheet] = []

    for sheet_idx, sheet_name in enumerate(wb.sheetnames):
        ws = wb[sheet_name]
        raw_rows = list(ws.iter_rows(values_only=True))
        if not raw_rows:
            continue

        header_row_idx, active_cols, unique_headers, banners = detect_header_and_columns(raw_rows)
        if not unique_headers or not active_cols:
            continue

        data_rows: list[list[Any]] = []
        for r in raw_rows[header_row_idx + 1 :]:
            row_vals = [r[c] if c < len(r) else None for c in active_cols]
            if not any(v is not None and str(v).strip() != "" for v in row_vals):
                continue
            data_rows.append(row_vals)

        table_ident = f"sheet_{sheet_idx + 1}_{sanitize_sql_ident(sheet_name)}"

        # Infer column types
        col_types: list[str] = []
        for col_i in range(len(unique_headers)):
            sample_col_vals = [data_rows[row_i][col_i] for row_i in range(min(500, len(data_rows)))]
            col_types.append(infer_duckdb_type(sample_col_vals))

        # Build DuckDB table
        sql_cols = []
        for h, t in zip(unique_headers, col_types, strict=False):
            sql_cols.append(f'"{sanitize_sql_ident(h)}" {t}')

        con.execute(f"CREATE TABLE {table_ident} ({', '.join(sql_cols)})")

        # Convert rows for insertion
        clean_rows = []
        for row in data_rows:
            clean_row = []
            for val, t in zip(row, col_types, strict=False):
                if val is None or val == "":
                    clean_row.append(None)
                elif t in ("DOUBLE", "BIGINT"):
                    try:
                        clean_str = str(val).replace(",", "").replace("$", "").replace("₹", "").replace("€", "").strip()
                        clean_row.append(float(clean_str) if t == "DOUBLE" else int(float(clean_str)))
                    except (ValueError, TypeError):
                        clean_row.append(None)
                elif isinstance(val, (datetime, date)):
                    clean_row.append(val.isoformat())
                else:
                    clean_row.append(str(val))
            clean_rows.append(clean_row)

        if clean_rows:
            placeholders = ", ".join(["?"] * len(unique_headers))
            con.executemany(f"INSERT INTO {table_ident} VALUES ({placeholders})", clean_rows)

        # Compute numeric summaries
        numeric_summaries: dict[str, dict[str, float]] = {}
        for h, t in zip(unique_headers, col_types, strict=False):
            if t in ("DOUBLE", "BIGINT"):
                ident = sanitize_sql_ident(h)
                try:
                    stats = con.execute(
                        f'SELECT SUM("{ident}"), AVG("{ident}"), MIN("{ident}"), MAX("{ident}") FROM {table_ident} WHERE "{ident}" IS NOT NULL'
                    ).fetchone()
                    if stats and stats[0] is not None:
                        numeric_summaries[h] = {
                            "sum": round(float(stats[0]), 2),
                            "avg": round(float(stats[1]), 2),
                            "min": round(float(stats[2]), 2),
                            "max": round(float(stats[3]), 2),
                        }
                except Exception:
                    pass

        parsed_sheets.append(
            ParsedSheet(
                sheet_name=sheet_name,
                table_name=table_ident,
                columns=unique_headers,
                rows=clean_rows,
                types=col_types,
                row_count=len(clean_rows),
                numeric_summaries=numeric_summaries,
                banners=banners,
            )
        )

    return ParsedSpreadsheet(filename=filename, sheets=parsed_sheets, duckdb_conn=con)


def parse_csv_bytes(payload: bytes, filename: str) -> ParsedSpreadsheet:
    """Parse a CSV or TSV into a ParsedSpreadsheet."""
    text = payload.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096]) if len(text) > 20 else csv.excel
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    raw_rows = [row for row in reader if any(cell.strip() for cell in row)]

    con = duckdb.connect(":memory:")
    if not raw_rows:
        return ParsedSpreadsheet(filename=filename, sheets=[], duckdb_conn=con)

    header_row_idx, active_cols, unique_headers, banners = detect_header_and_columns(raw_rows)
    if not unique_headers or not active_cols:
        return ParsedSpreadsheet(filename=filename, sheets=[], duckdb_conn=con)

    data_rows: list[list[Any]] = []
    for r in raw_rows[header_row_idx + 1 :]:
        row_vals = [r[c] if c < len(r) else None for c in active_cols]
        if not any(v is not None and str(v).strip() != "" for v in row_vals):
            continue
        data_rows.append(row_vals)

    col_types = []
    for col_i in range(len(unique_headers)):
        sample_vals = [data_rows[row_i][col_i] for row_i in range(min(500, len(data_rows)))]
        col_types.append(infer_duckdb_type(sample_vals))

    table_ident = f"csv_{sanitize_sql_ident(filename)}"
    sql_cols = [f'"{sanitize_sql_ident(h)}" {t}' for h, t in zip(unique_headers, col_types, strict=False)]
    con.execute(f"CREATE TABLE {table_ident} ({', '.join(sql_cols)})")

    clean_rows = []
    for row in data_rows:
        clean_row = []
        for val, t in zip(row, col_types, strict=False):
            if val is None or val == "":
                clean_row.append(None)
            elif t in ("DOUBLE", "BIGINT"):
                try:
                    clean_str = str(val).replace(",", "").replace("$", "").replace("₹", "").replace("€", "").strip()
                    clean_row.append(float(clean_str) if t == "DOUBLE" else int(float(clean_str)))
                except (ValueError, TypeError):
                    clean_row.append(None)
            else:
                clean_row.append(str(val))
        clean_rows.append(clean_row)

    if clean_rows:
        placeholders = ", ".join(["?"] * len(unique_headers))
        con.executemany(f"INSERT INTO {table_ident} VALUES ({placeholders})", clean_rows)

    numeric_summaries: dict[str, dict[str, float]] = {}
    for h, t in zip(unique_headers, col_types, strict=False):
        if t in ("DOUBLE", "BIGINT"):
            ident = sanitize_sql_ident(h)
            try:
                stats = con.execute(
                    f'SELECT SUM("{ident}"), AVG("{ident}"), MIN("{ident}"), MAX("{ident}") FROM {table_ident} WHERE "{ident}" IS NOT NULL'
                ).fetchone()
                if stats and stats[0] is not None:
                    numeric_summaries[h] = {
                        "sum": round(float(stats[0]), 2),
                        "avg": round(float(stats[1]), 2),
                        "min": round(float(stats[2]), 2),
                        "max": round(float(stats[3]), 2),
                    }
            except Exception:
                pass

    sheet = ParsedSheet(
        sheet_name="Main",
        table_name=table_ident,
        columns=unique_headers,
        rows=clean_rows,
        types=col_types,
        row_count=len(clean_rows),
        numeric_summaries=numeric_summaries,
        banners=banners,
    )
    return ParsedSpreadsheet(filename=filename, sheets=[sheet], duckdb_conn=con)


def parse_document_bytes(payload: bytes, filename: str) -> ParsedDocument:
    """Parse PDF, DOCX, TXT, MD, or JSON into structured document sections."""
    name = filename.lower()
    sections: list[ParsedDocSection] = []
    full_text = ""

    if name.endswith(".pdf"):
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(payload))
        for page_idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            page_clean = page_text.strip()
            if page_clean:
                sections.append(
                    ParsedDocSection(
                        section_index=page_idx + 1,
                        title=f"Page {page_idx + 1}",
                        page=page_idx + 1,
                        text=page_clean,
                    )
                )
        full_text = "\n\n".join(f"[Page {s.page}]\n{s.text}" for s in sections)

    elif name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as docx:
                xml_content = docx.read("word/document.xml")
                tree = ET.fromstring(xml_content)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                paragraphs = []
                for p in tree.iter(f"{{{ns['w']}}}p"):
                    texts = [node.text for node in p.iter(f"{{{ns['w']}}}t") if node.text]
                    if texts:
                        paragraphs.append("".join(texts).strip())
                for i, p in enumerate(paragraphs):
                    if p:
                        sections.append(
                            ParsedDocSection(
                                section_index=i + 1,
                                title=f"Paragraph {i + 1}",
                                page=None,
                                text=p,
                            )
                        )
                full_text = "\n\n".join(p for p in paragraphs if p)
        except Exception:
            full_text = "[Could not parse Word document XML.]"

    elif name.endswith(".json"):
        import json

        try:
            data = json.loads(payload.decode("utf-8-sig", errors="replace"))
            if isinstance(data, list):
                for i, item in enumerate(data):
                    if isinstance(item, dict):
                        title = (
                            item.get("title")
                            or item.get("name")
                            or item.get("doc_id")
                            or item.get("id")
                            or f"Record {i + 1}"
                        )
                        body = (
                            item.get("body")
                            or item.get("description")
                            or item.get("summary")
                            or item.get("text")
                            or item.get("content")
                            or item.get("message")
                        )
                        meta_items = []
                        if item.get("period"):
                            meta_items.append(f"Period: {item['period']}")
                        if item.get("doc_type"):
                            meta_items.append(f"Type: {item['doc_type']}")
                        if item.get("region"):
                            meta_items.append(f"Region: {item['region']}")
                        if item.get("category"):
                            meta_items.append(f"Category: {item['category']}")
                        if item.get("author_role"):
                            meta_items.append(f"Author: {item['author_role']}")
                        if item.get("created_ts"):
                            meta_items.append(f"Date: {str(item['created_ts'])[:10]}")

                        prefix = f"[{', '.join(meta_items)}] " if meta_items else ""
                        if not body:
                            body = ", ".join(
                                f"{k}: {v}"
                                for k, v in item.items()
                                if v is not None and k not in ("title", "name")
                            )

                        full_item_text = f"{prefix}{body}".strip()
                        sections.append(
                            ParsedDocSection(
                                section_index=i + 1,
                                title=str(title),
                                page=None,
                                text=full_item_text,
                            )
                        )
                    else:
                        sections.append(
                            ParsedDocSection(
                                section_index=i + 1,
                                title=f"Item {i + 1}",
                                page=None,
                                text=str(item),
                            )
                        )
                full_text = "\n\n".join(f"## {s.title}\n{s.text}" for s in sections)
                return ParsedDocument(filename=filename, full_text=full_text, sections=sections)
            elif isinstance(data, dict):
                for k, v in data.items():
                    val_str = json.dumps(v, indent=2) if isinstance(v, (dict, list)) else str(v)
                    sections.append(
                        ParsedDocSection(
                            section_index=len(sections) + 1,
                            title=str(k),
                            page=None,
                            text=val_str,
                        )
                    )
                full_text = "\n\n".join(f"## {s.title}\n{s.text}" for s in sections)
                return ParsedDocument(filename=filename, full_text=full_text, sections=sections)
        except Exception:
            pass
        decoded = payload.decode("utf-8-sig", errors="replace").strip()
        full_text = decoded
        sections.append(ParsedDocSection(section_index=1, title="JSON Content", page=None, text=decoded))

    else:
        # Text, Markdown, etc.
        decoded = payload.decode("utf-8-sig", errors="replace").strip()
        full_text = decoded
        # Split by markdown headings or double newlines
        parts = re.split(r"\n(?=#{1,3}\s+)", decoded) if "#" in decoded else decoded.split("\n\n")
        for i, part in enumerate(parts):
            p_clean = part.strip()
            if p_clean:
                first_line = p_clean.split("\n")[0][:60]
                # Filter out raw bracket lines as titles
                clean_title = first_line if first_line and first_line not in ("[", "{", "]", "}") else f"Section {i + 1}"
                sections.append(
                    ParsedDocSection(
                        section_index=i + 1,
                        title=clean_title,
                        page=None,
                        text=p_clean,
                    )
                )

    return ParsedDocument(filename=filename, full_text=full_text, sections=sections)


def answer_spreadsheet_query(
    question: str,
    spreadsheet: ParsedSpreadsheet,
    history: list[dict] | None = None,
) -> AnswerEnvelope:
    """Analyze a user question and compute grounded answers directly over the uploaded spreadsheet."""
    if not spreadsheet.sheets:
        return AnswerEnvelope(
            answer=f"The uploaded spreadsheet `{spreadsheet.filename}` is empty or could not be read.",
            route="structured",
            confidence="low",
            caveats=["Spreadsheet contained no readable data."],
        )

    con = spreadsheet.duckdb_conn
    # Extract the user's actual question text, ignoring appended attachment context
    clean_q = question.split("[Attached file:")[0].strip() if "[Attached file:" in question else question
    q_lower = clean_q.lower()
    target_sheet = spreadsheet.sheets[0]
    for s in spreadsheet.sheets:
        if s.sheet_name.lower() in q_lower:
            target_sheet = s
            break

    table_name = target_sheet.table_name
    cols = target_sheet.columns
    types = target_sheet.types
    numeric_cols = [c for c, t in zip(cols, types, strict=False) if t in ("DOUBLE", "BIGINT")]
    text_cols = [c for c, t in zip(cols, types, strict=False) if t == "VARCHAR"]

    # Match mentioned numeric columns
    matched_num_cols = [c for c in numeric_cols if re.search(r"\b" + re.escape(c.lower()) + r"\b", q_lower)]
    # Match mentioned text/category columns
    matched_cat_cols = [c for c in text_cols if re.search(r"\b" + re.escape(c.lower()) + r"\b", q_lower)]

    sql_statements: list[str] = []
    tables: list[Table] = []
    chart: Chart | None = None
    answer_parts: list[str] = []

    # Check for aggregation questions (sum, total, average, max, min)
    is_price = bool(re.search(r"\b(price|prices|pricing|rate|rates|cost|costs|mrp|invoice|margin|expensive|priciest|costly|cheapest)\b", q_lower))
    is_list = bool(re.search(r"\b(list|show|display|get|all|table|items|products|catalog|give me|what are|view)\b", q_lower))
    is_sum = bool(re.search(r"\b(total|sum|sum of|aggregate)\b", q_lower)) or (
        bool(re.search(r"\b(overall|entire)\b", q_lower)) and not is_list
    )
    is_avg = bool(re.search(r"\b(average|avg|mean)\b", q_lower))
    is_max = bool(re.search(r"\b(highest|maximum|max|top|best|expensive|priciest|costly|most)\b", q_lower))
    is_min = bool(re.search(r"\b(lowest|minimum|min|bottom|worst|cheapest|least)\b", q_lower))
    is_count = bool(re.search(r"\b(how many|count|number of rows|number of items|records)\b", q_lower))
    is_group = bool(re.search(r"\b(by|per|breakdown|grouped by)\b", q_lower)) and (matched_cat_cols or len(text_cols) > 0)

    # A: Grouped breakdown (e.g., "sales by region" or "profit by category")
    if is_group and (matched_num_cols or numeric_cols):
        cat_col = matched_cat_cols[0] if matched_cat_cols else text_cols[0]
        num_col = matched_num_cols[0] if matched_num_cols else numeric_cols[0]
        cat_ident = sanitize_sql_ident(cat_col)
        num_ident = sanitize_sql_ident(num_col)

        agg_func = "AVG" if is_avg else "SUM"
        agg_label = f"Average {num_col}" if is_avg else f"Total {num_col}"

        sql = (
            f'SELECT "{cat_ident}" AS "{cat_col}", {agg_func}("{num_ident}") AS "{agg_label}" '
            f"FROM {table_name} "
            f'WHERE "{cat_ident}" IS NOT NULL AND "{num_ident}" IS NOT NULL '
            f'GROUP BY "{cat_ident}" '
            f'ORDER BY "{agg_label}" DESC '
            f"LIMIT 20"
        )
        sql_statements.append(sql)
        res = con.execute(sql).fetchall()

        table_rows = [[r[0], round(float(r[1]), 2) if r[1] is not None else 0.0] for r in res]
        t = Table(
            title=f"{agg_label} by {cat_col} ({spreadsheet.filename})",
            columns=[cat_col, agg_label],
            rows=table_rows,
        )
        tables.append(t)

        chart = Chart(
            type="bar",
            x=cat_col,
            series=[ChartSeries(name=agg_label, y=agg_label)],
            data_ref="tables[0]",
        )

        top_preview = ", ".join(f"{r[0]}: {r[1]:,}" for r in table_rows[:3])
        answer_parts.append(
            f"Computed {agg_label.lower()} grouped by **{cat_col}** from `{spreadsheet.filename}` (Sheet: '{target_sheet.sheet_name}'). "
            f"Top contributors: {top_preview}."
        )

    # B: Top / Highest / Lowest single or ranked list
    elif (is_max or is_min) and (matched_num_cols or numeric_cols):
        price_cols = [c for c in cols if any(k in c.lower() for k in ("mrp", "price", "cost", "rate", "invoice")) and c in numeric_cols]
        if matched_num_cols:
            num_col = matched_num_cols[0]
        elif is_price and price_cols:
            num_col = "MRP" if "MRP" in price_cols else price_cols[0]
        else:
            num_col = numeric_cols[0]

        num_ident = sanitize_sql_ident(num_col)
        order = "DESC" if is_max else "ASC"
        order_word = "Highest" if is_max else "Lowest"

        sql = (
            f"SELECT * FROM {table_name} "
            f'WHERE "{num_ident}" IS NOT NULL '
            f'ORDER BY "{num_ident}" {order} '
            f"LIMIT 10"
        )
        sql_statements.append(sql)
        rows_raw = con.execute(sql).fetchall()
        t = Table(
            title=f"{order_word} records by {num_col} ({spreadsheet.filename})",
            columns=cols,
            rows=[list(r) for r in rows_raw],
        )
        tables.append(t)

        best_row = rows_raw[0] if rows_raw else None
        if best_row:
            val_idx = cols.index(num_col)
            val = best_row[val_idx]
            ident_col = next((c for c in cols if any(k in c.lower() for k in ("desc", "description", "item", "product", "name", "particulars"))), None)
            item_name = str(best_row[cols.index(ident_col)]) if ident_col else "Item"
            weight_col = next((c for c in cols if any(k in c.lower() for k in ("wht", "weight", "gms", "kg"))), None)
            weight_val = f" ({best_row[cols.index(weight_col)]}g)" if weight_col and best_row[cols.index(weight_col)] is not None else ""
            is_money = any(k in num_col.lower() for k in ("price", "mrp", "invoice", "margin", "cost", "rate", "amount"))
            val_formatted = f"₹{val:,.2f}" if is_money and isinstance(val, (int, float)) else f"{val:,}"

            answer_parts.append(
                f"The {order_word.lower()} **{num_col}** in `{spreadsheet.filename}` is **{val_formatted}** for **{item_name}{weight_val}** "
                f"(Sheet: '{target_sheet.sheet_name}')."
            )

            # Details breakdown of that specific top/bottom product
            item_details = []
            for c in cols:
                if c not in (num_col, ident_col, weight_col) and best_row[cols.index(c)] is not None:
                    c_val = best_row[cols.index(c)]
                    c_money = any(k in c.lower() for k in ("price", "mrp", "invoice", "margin", "cost", "rate", "amount"))
                    c_fmt = f"₹{c_val:,.2f}" if c_money and isinstance(c_val, (int, float)) else str(c_val)
                    item_details.append(f"- **{c}**: {c_fmt}")
            if item_details:
                answer_parts.append("\n**Product Details:**\n" + "\n".join(item_details[:6]))

    # C: Specific numeric aggregate (Total or Average of column)
    elif (is_sum or is_avg or matched_num_cols) and not is_price and (matched_num_cols or numeric_cols):
        target_num = matched_num_cols[0] if matched_num_cols else numeric_cols[0]
        ident = sanitize_sql_ident(target_num)
        agg_func = "AVG" if is_avg else "SUM"
        agg_name = "Average" if is_avg else "Total"

        sql = f'SELECT {agg_func}("{ident}"), MIN("{ident}"), MAX("{ident}"), COUNT("{ident}") FROM {table_name}'
        sql_statements.append(sql)
        row = con.execute(sql).fetchone()
        agg_val = round(float(row[0]), 2) if row and row[0] is not None else 0.0
        min_val = round(float(row[1]), 2) if row and row[1] is not None else 0.0
        max_val = round(float(row[2]), 2) if row and row[2] is not None else 0.0
        cnt_val = int(row[3]) if row and row[3] is not None else 0

        t = Table(
            title=f"Summary of {target_num} ({spreadsheet.filename})",
            columns=["Metric", "Value"],
            rows=[
                [f"{agg_name} {target_num}", f"{agg_val:,}"],
                ["Minimum", f"{min_val:,}"],
                ["Maximum", f"{max_val:,}"],
                ["Record Count", f"{cnt_val:,}"],
            ],
        )
        tables.append(t)
        answer_parts.append(
            f"The **{agg_name.lower()} {target_num}** in `{spreadsheet.filename}` (Sheet: '{target_sheet.sheet_name}') "
            f"is **{agg_val:,.2f}** across {cnt_val:,} records (ranging from {min_val:,} to {max_val:,})."
        )

    # D: Price / List / Full Catalog Table query
    elif (is_price or is_list) and not any(re.findall(r"\b\d+(?:\.\d+)?\b", q_lower)):
        price_keywords = {"price", "rate", "cost", "mrp", "invoice", "margin", "amount", "total"}
        ident_keywords = {
            "no", "sno", "id", "code", "sku", "name", "desc", "description",
            "item", "product", "particulars", "wht", "weight", "gms", "kg", "case", "qty", "quantity",
        }

        has_price_cols = [c for c in cols if any(pk in c.lower() for pk in price_keywords)]
        has_ident_cols = [c for c in cols if any(ik in c.lower() for ik in ident_keywords)]

        # Select all columns if small table (<= 12 columns), else select ident + price cols
        if len(cols) <= 12:
            selected_cols = cols
        elif has_price_cols and is_price:
            wanted_cols = set(has_ident_cols + has_price_cols)
            selected_cols = [c for c in cols if c in wanted_cols] or cols[:8]
        else:
            selected_cols = cols[:8]

        sql_cols_str = ", ".join(f'"{sanitize_sql_ident(c)}" AS "{c}"' for c in selected_cols)
        sql = f"SELECT {sql_cols_str} FROM {table_name} LIMIT 100"
        sql_statements.append(sql)
        query_rows = con.execute(sql).fetchall()

        t = Table(
            title=(
                f"Price List: {target_sheet.sheet_name} ({spreadsheet.filename})"
                if is_price
                else f"{target_sheet.sheet_name} ({spreadsheet.filename})"
            ),
            columns=selected_cols,
            rows=[list(r) for r in query_rows],
        )
        tables.append(t)

        banner_prefix = f" (*{' - '.join(target_sheet.banners[:2])}*)" if target_sheet.banners else ""
        intro = (
            f"Here is the complete price list from **{spreadsheet.filename}**{banner_prefix}:"
            if is_price
            else f"Here are the records from **{spreadsheet.filename}** (Sheet: '{target_sheet.sheet_name}'){banner_prefix}:"
        )
        answer_parts.append(intro)

        # Build clean Markdown table
        md_table_lines = [
            "| " + " | ".join(selected_cols) + " |",
            "| " + " | ".join(["---"] * len(selected_cols)) + " |",
        ]
        for r in query_rows:
            formatted_cells = []
            for cell_val, col_name in zip(r, selected_cols, strict=False):
                if cell_val is None or cell_val == "":
                    formatted_cells.append("—")
                elif isinstance(cell_val, (int, float)):
                    c_low = col_name.lower()
                    is_money = any(k in c_low for k in ("price", "mrp", "invoice", "margin", "cost", "rate", "amount"))
                    if is_money:
                        formatted_cells.append(f"₹{cell_val:,.2f}")
                    elif isinstance(cell_val, int) or float(cell_val).is_integer():
                        formatted_cells.append(f"{int(cell_val):,}")
                    else:
                        formatted_cells.append(f"{cell_val:,.2f}")
                else:
                    formatted_cells.append(str(cell_val).strip())
            md_table_lines.append("| " + " | ".join(formatted_cells) + " |")

        answer_parts.append("\n" + "\n".join(md_table_lines) + "\n")

        # Highlights for price columns
        if has_price_cols:
            highlights = []
            for pc in has_price_cols:
                p_ident = sanitize_sql_ident(pc)
                try:
                    stats = con.execute(
                        f'SELECT MIN("{p_ident}"), MAX("{p_ident}"), AVG("{p_ident}") FROM {table_name} WHERE "{p_ident}" IS NOT NULL'
                    ).fetchone()
                    if stats and stats[0] is not None:
                        min_p = float(stats[0])
                        max_p = float(stats[1])
                        avg_p = float(stats[2])
                        if min_p == max_p:
                            highlights.append(f"- **{pc}**: ₹{min_p:,.2f}")
                        else:
                            highlights.append(f"- **{pc}**: ₹{min_p:,.2f} to ₹{max_p:,.2f} (Avg: ₹{avg_p:,.2f})")
                except Exception:
                    pass
            if highlights:
                answer_parts.append("**Pricing Highlights:**\n" + "\n".join(highlights))

    # E: Specific Item / Filter Search (e.g. "what is the price of 100g?", "show plain papad 70", "how much is 150g?")
    elif any(re.findall(r"\b\d+(?:\.\d+)?\b", q_lower)) or any(
        any(tok in str(c_val).lower() for c_val in r)
        for r in target_sheet.rows[:20]
        for tok in re.findall(r"[a-zA-Z0-9]{3,}", q_lower)
        if tok not in {"what", "which", "the", "price", "prices", "for", "and", "show", "how", "much", "tell", "about", "give", "list"}
    ):
        numbers_in_q = re.findall(r"\b\d+(?:\.\d+)?\b", q_lower)
        where_clauses: list[str] = []
        for num_str in numbers_in_q:
            try:
                num_v = float(num_str)
                for nc in numeric_cols:
                    where_clauses.append(f'"{sanitize_sql_ident(nc)}" = {num_v}')
            except ValueError:
                pass

        text_words = [
            w for w in re.findall(r"[a-zA-Z]{3,}", q_lower)
            if w not in {
                "what", "which", "the", "price", "prices", "for", "and", "show", "how", "much",
                "tell", "about", "give", "list", "with", "this", "that", "from", "sheet", "file",
                "product", "products", "item", "items", "retailer", "distributor"
            }
        ]
        for tw in text_words:
            for tc in text_cols:
                where_clauses.append(f'lower("{sanitize_sql_ident(tc)}") LIKE \'%{tw}%\'')

        filter_sql = (
            f"SELECT * FROM {table_name} WHERE {' OR '.join(where_clauses)} LIMIT 10"
            if where_clauses
            else f"SELECT * FROM {table_name} LIMIT 10"
        )
        sql_statements.append(filter_sql)
        filtered_rows = con.execute(filter_sql).fetchall()

        if filtered_rows:
            t = Table(
                title=f"Filtered Results: {target_sheet.sheet_name} ({spreadsheet.filename})",
                columns=cols,
                rows=[list(r) for r in filtered_rows],
            )
            tables.append(t)

            ident_col = next((c for c in cols if any(k in c.lower() for k in ("desc", "description", "item", "product", "name", "particulars"))), None)
            weight_col = next((c for c in cols if any(k in c.lower() for k in ("wht", "weight", "gms", "kg"))), None)

            answer_parts.append(f"Found **{len(filtered_rows)} matching product(s)** in `{spreadsheet.filename}`:")
            for r in filtered_rows[:3]:
                item_name = str(r[cols.index(ident_col)]) if ident_col else "Product"
                weight_val = f" ({r[cols.index(weight_col)]}g)" if weight_col and r[cols.index(weight_col)] is not None else ""
                answer_parts.append(f"\n### {item_name}{weight_val}")
                for c in cols:
                    if c not in (ident_col, weight_col) and r[cols.index(c)] is not None:
                        c_val = r[cols.index(c)]
                        is_m = any(k in c.lower() for k in ("price", "mrp", "invoice", "margin", "cost", "rate", "amount"))
                        f_val = f"₹{c_val:,.2f}" if is_m and isinstance(c_val, (int, float)) else str(c_val)
                        answer_parts.append(f"- **{c}**: {f_val}")
        else:
            # Fallback to preview if no filter match
            sql = f"SELECT * FROM {table_name} LIMIT 10"
            sql_statements.append(sql)
            preview_rows = con.execute(sql).fetchall()
            t = Table(
                title=f"Data Preview: {target_sheet.sheet_name} ({spreadsheet.filename})",
                columns=cols,
                rows=[list(r) for r in preview_rows],
            )
            tables.append(t)
            answer_parts.append(f"No exact match was found for '{clean_q}'. Here is the data preview from `{spreadsheet.filename}`.")

    # F: Count / Overview / General Analysis
    else:
        sql = f"SELECT * FROM {table_name} LIMIT 10"
        sql_statements.append(sql)
        preview_rows = con.execute(sql).fetchall()

        t = Table(
            title=f"Data Preview: {target_sheet.sheet_name} ({spreadsheet.filename})",
            columns=cols,
            rows=[list(r) for r in preview_rows],
        )
        tables.append(t)

        summaries_text = []
        for col_name, stats in target_sheet.numeric_summaries.items():
            summaries_text.append(f"**{col_name}**: Total {stats['sum']:,} (Avg {stats['avg']:,})")

        sum_block = "; ".join(summaries_text[:4]) if summaries_text else "None"
        banner_context = f" (*{' - '.join(target_sheet.banners[:2])}*)" if target_sheet.banners else ""
        answer_parts.append(
            f"Loaded and analyzed `{spreadsheet.filename}` (Sheet: '{target_sheet.sheet_name}'){banner_context}. "
            f"It contains **{target_sheet.row_count:,} rows** and **{len(cols)} columns** ({', '.join(cols[:6])}). "
            f"Key metrics: {sum_block}."
        )

    citation = Citation(
        n=1,
        doc_id=f"upload_{sanitize_sql_ident(spreadsheet.filename)}",
        source_type="excel" if spreadsheet.filename.lower().endswith((".xlsx", ".xls")) else "csv",
        title=f"{spreadsheet.filename} ({target_sheet.sheet_name})",
        date=date.today().isoformat(),
        score=1.0,
    )

    return AnswerEnvelope(
        answer="\n\n".join(answer_parts),
        route="structured",
        sql=sql_statements,
        tables=tables,
        citations=[citation],
        chart=chart,
        confidence="high",
        caveats=[],
    )


def answer_document_query(
    question: str,
    document: ParsedDocument,
    history: list[dict] | None = None,
) -> AnswerEnvelope:
    """Answer a user question based on the content of an uploaded text document or PDF."""
    if not document.sections or not document.full_text.strip():
        return AnswerEnvelope(
            answer=f"The uploaded document `{document.filename}` was empty or contained no extractable text.",
            route="unstructured",
            confidence="low",
            caveats=["Document text extraction yielded no content."],
        )

    q_lower = question.lower()
    q_tokens = set(re.findall(r"\b\w{3,}\b", q_lower))

    # Incorporate contextual keywords from recent history if question is short or refers to previous items
    if history and (len(q_tokens) <= 3 or any(p in q_lower for p in ("it", "that", "this", "they", "them", "more", "previous"))):
        for msg in reversed(history[-4:]):
            text = msg.get("content", "") or msg.get("text", "")
            hist_tokens = [w for w in re.findall(r"\b\w{3,}\b", text.lower())]
            q_tokens.update(hist_tokens[:5])

    # Score each section by token overlap
    scored_sections: list[tuple[float, ParsedDocSection]] = []
    for s in document.sections:
        s_tokens = set(re.findall(r"\b\w{3,}\b", s.text.lower()))
        overlap = len(q_tokens & s_tokens)
        score = overlap / max(1, len(q_tokens))
        scored_sections.append((score, s))

    scored_sections.sort(key=lambda x: x[0], reverse=True)

    is_summary = any(
        w in q_lower
        for w in (
            "summar", "overview", "what is this", "about", "explain", "digest",
            "key points", "main points", "takeaways", "highlight",
        )
    )

    citations: list[Citation] = []
    answer_parts: list[str] = []

    if is_summary or scored_sections[0][0] == 0:
        # Document summary mode
        doc_sample_sections = document.sections[:8]
        highlights = []
        for i, s in enumerate(doc_sample_sections):
            snippet = s.text.replace("\n", " ").strip()
            label = s.title if s.title and s.title not in ("[", "{", "]", "}", "null") else f"Item {i + 1}"
            highlights.append(f"- **{label}** [{i + 1}]\n  {snippet}")
            citations.append(
                Citation(
                    n=i + 1,
                    doc_id=f"doc_{i + 1}",
                    source_type="document",
                    title=f"{document.filename} ({label})",
                    date=date.today().isoformat(),
                    score=1.0,
                )
            )

        answer_parts.append(
            f"Here is a summary of the uploaded document `{document.filename}` "
            f"({len(document.sections)} record(s)/section(s)):\n\n"
            + "\n\n".join(highlights)
        )
    else:
        # Specific Q&A mode: Extract top matching sections
        top_matches = [s for score, s in scored_sections if score > 0][:3]
        if not top_matches:
            top_matches = [scored_sections[0][1]]

        answer_parts.append(
            f"Based on `{document.filename}`, here are the relevant details for your question:\n"
        )
        for i, match in enumerate(top_matches):
            label = match.title if match.title and match.title not in ("[", "{", "]", "}") else f"Section {match.section_index}"
            cite_num = i + 1
            snippet = match.text.replace("\n", " ").strip()
            if len(snippet) > 400:
                # Find sentence containing query tokens
                sentences = re.split(r"(?<=[.!?])\s+", snippet)
                matching_s = [sent for sent in sentences if any(tok in sent.lower() for tok in q_tokens)]
                if matching_s:
                    snippet = " ".join(matching_s[:3])
                else:
                    snippet = snippet[:400] + "…"

            answer_parts.append(f"\n> \"{snippet}\" [{cite_num}]")
            citations.append(
                Citation(
                    n=cite_num,
                    doc_id=f"doc_cite_{cite_num}",
                    source_type="document",
                    title=f"{document.filename} ({label})",
                    date=date.today().isoformat(),
                    score=round(scored_sections[i][0], 2),
                )
            )

    return AnswerEnvelope(
        answer="\n".join(answer_parts),
        route="unstructured",
        sql=[],
        tables=[],
        citations=citations,
        confidence="high",
        caveats=[],
    )


def answer_attachments_query(
    question: str,
    attachments: list[dict],
    history: list[dict] | None = None,
) -> AnswerEnvelope | None:
    """Process single or multiple uploaded files (spreadsheets, documents, json datasets)."""
    if not attachments:
        return None

    spreadsheets: list[ParsedSpreadsheet] = []
    documents: list[ParsedDocument] = []

    for att in attachments:
        att_name = str(att.get("name", "")).strip()
        att_name_lower = att_name.lower()
        raw_bytes = att.get("raw_bytes")
        if raw_bytes is None and att.get("data") and att.get("kind") != "image":
            raw_bytes = att["data"].encode("utf-8", errors="replace")

        if not raw_bytes:
            continue

        if att_name_lower.endswith((".xlsx", ".xls")):
            try:
                spreadsheets.append(parse_excel_bytes(raw_bytes, att_name))
            except Exception:
                pass
        elif att_name_lower.endswith((".csv", ".tsv")):
            try:
                spreadsheets.append(parse_csv_bytes(raw_bytes, att_name))
            except Exception:
                pass
        elif att_name_lower.endswith((".pdf", ".docx", ".txt", ".md", ".json")):
            try:
                documents.append(parse_document_bytes(raw_bytes, att_name))
            except Exception:
                pass

    if not spreadsheets and not documents:
        return None

    # Single spreadsheet
    if len(spreadsheets) == 1 and not documents:
        return answer_spreadsheet_query(question, spreadsheets[0], history=history)

    # Single document
    if len(documents) == 1 and not spreadsheets:
        return answer_document_query(question, documents[0], history=history)

    # Multiple files (spreadsheets and/or documents)
    all_answers: list[str] = [f"### Summary of {len(spreadsheets) + len(documents)} Uploaded Files\n"]
    all_sql: list[str] = []
    all_tables: list[Table] = []
    all_citations: list[Citation] = []
    chart: Chart | None = None

    cite_counter = 1
    for s in spreadsheets:
        res = answer_spreadsheet_query(question, s, history=history)
        all_answers.append(f"#### 📊 `{s.filename}`\n{res.answer}\n")
        all_sql.extend(res.sql)
        all_tables.extend(res.tables)
        if not chart and res.chart:
            chart = res.chart
        for c in res.citations:
            all_citations.append(
                Citation(
                    n=cite_counter,
                    doc_id=c.doc_id,
                    source_type=c.source_type,
                    title=c.title,
                    date=c.date,
                    score=c.score,
                )
            )
            cite_counter += 1

    for d in documents:
        res = answer_document_query(question, d, history=history)
        all_answers.append(f"#### 📄 `{d.filename}`\n{res.answer}\n")
        all_tables.extend(res.tables)
        for c in res.citations:
            all_citations.append(
                Citation(
                    n=cite_counter,
                    doc_id=c.doc_id,
                    source_type=c.source_type,
                    title=c.title,
                    date=c.date,
                    score=c.score,
                )
            )
            cite_counter += 1

    return AnswerEnvelope(
        answer="\n".join(all_answers).strip(),
        route="hybrid" if (all_sql or all_tables) else "unstructured",
        sql=all_sql,
        tables=all_tables,
        citations=all_citations,
        chart=chart,
        confidence="high",
        caveats=[],
    )
