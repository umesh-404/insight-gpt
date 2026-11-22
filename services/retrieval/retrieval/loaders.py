"""Uploaded file -> plain text, so the existing pipeline can index it.

An upload is only ever turned into *text* here. Everything after that —
redaction, chunking, embedding, storage — is the same code path the curated
corpus uses, because a second pipeline is a second place for redaction to be
forgotten (contract §6.4).

Two shapes, chosen by extension:

* **document** (``.txt .md .json .pdf``) — the text as written.
* **tabular** (``.csv .tsv .xlsx``) — rendered as one ``column: value`` block
  per row under a column summary. Rows are rendered as *lines*, and the
  chunker windows on line boundaries, so a row is never split down the middle
  and every retrieved chunk is a whole number of records.

Why not embed a raw CSV verbatim? ``2026-04-02,X230,North,1899`` embeds almost
no meaning: the column names are a hundred lines away in the header. Repeating
the field name next to every value is more tokens and far better retrieval —
and it is what makes a cited chunk readable to the person who asked.

Honesty note: rendering a table as text makes it *retrievable*, not
*queryable*. An uploaded spreadsheet does not become a governed metric and is
never aggregated as one (contract §6.1); questions about it are answered from
its text, with the dataset named.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

# Extensions the datasets path accepts, split by how they are read. Kept here
# so the API and the indexer cannot disagree about what is supported.
DOCUMENT_EXTENSIONS: tuple[str, ...] = (".txt", ".md", ".json", ".pdf", ".docx")
TABULAR_EXTENSIONS: tuple[str, ...] = (".csv", ".tsv", ".xlsx")
SUPPORTED_EXTENSIONS: tuple[str, ...] = DOCUMENT_EXTENSIONS + TABULAR_EXTENSIONS

# Rows rendered into the text document. A dataset is for retrieval, not for
# analytics, and 5000 rows of rendered fields is already far more text than any
# number of questions can retrieve from. The cap is reported, never silent.
MAX_TABULAR_ROWS = 5000


class UnsupportedDatasetFile(ValueError):
    """The file's extension is not one this loader can read."""


class DatasetExtractionError(RuntimeError):
    """The file matched a supported extension but could not be read."""


def classify(path: str | Path) -> str:
    """``"tabular"`` or ``"document"`` — the ``kind`` the API reports.

    Raises :class:`UnsupportedDatasetFile` naming what *is* accepted, so the
    caller can turn it into a 400 whose message is actually useful.
    """
    suffix = Path(path).suffix.lower()
    if suffix in TABULAR_EXTENSIONS:
        return "tabular"
    if suffix in DOCUMENT_EXTENSIONS:
        return "document"
    raise UnsupportedDatasetFile(
        f"unsupported file type {suffix or '(none)'}; accepted: "
        f"{', '.join(SUPPORTED_EXTENSIONS)}"
    )


def extract_text(path: str | Path, *, name: str | None = None) -> tuple[str, str, list[str]]:
    """Read one uploaded file as ``(text, kind, notes)``.

    ``notes`` carries anything the caller should be told rather than have
    silently applied — a truncated row count, a PDF page that yielded no text.
    An empty result is returned as empty text, not as a fabricated placeholder
    (contract §6.3); the caller decides whether that is a failure.
    """
    path = Path(path)
    title = name or path.name
    kind = classify(path)
    if not path.exists():
        raise DatasetExtractionError(f"file not found: {path}")

    suffix = path.suffix.lower()
    try:
        if suffix in (".csv", ".tsv"):
            text, notes = _from_delimited(path, title, delimiter="\t" if suffix == ".tsv" else ",")
        elif suffix == ".xlsx":
            text, notes = _from_xlsx(path, title)
        elif suffix == ".pdf":
            text, notes = _from_pdf(path)
        elif suffix == ".docx":
            text, notes = _from_docx(path), []
        elif suffix == ".json":
            text, notes = _from_json(path, title)
        else:
            text, notes = _read_text(path), []
    except (UnsupportedDatasetFile, DatasetExtractionError):
        raise
    except Exception as exc:  # a corrupt upload must be a clean failure
        raise DatasetExtractionError(
            f"could not read {title}: {type(exc).__name__}: {exc}"
        ) from exc

    return text, kind, notes


# ------------------------------------------------------------------ plain text
def _read_text(path: Path) -> str:
    """Decode as UTF-8, falling back to latin-1 rather than failing an upload.

    ``errors="replace"`` would quietly pepper the text with U+FFFD; latin-1
    always decodes, so a mis-labelled encoding costs a few odd characters
    instead of a failed dataset.
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# --------------------------------------------------------------------- tabular
def _render_rows(title: str, columns: list[str], rows: list[list[object]]) -> tuple[str, list[str]]:
    """Render a table as a column summary plus one labelled block per row."""
    notes: list[str] = []
    if len(rows) > MAX_TABULAR_ROWS:
        notes.append(
            f"indexed the first {MAX_TABULAR_ROWS} of {len(rows)} rows "
            f"(rows beyond that are not retrievable)"
        )
        rows = rows[:MAX_TABULAR_ROWS]

    header = [f"# {title}", f"Columns ({len(columns)}): {', '.join(columns)}",
              f"Rows indexed: {len(rows)}", ""]
    lines = list(header)
    for i, row in enumerate(rows, 1):
        fields = [
            f"{col}: {value}"
            for col, value in zip(columns, row, strict=False)
            if value is not None and str(value).strip() != ""
        ]
        if fields:
            lines.append(f"Row {i} — " + " | ".join(fields))
    return "\n".join(lines), notes


def _from_delimited(path: Path, title: str, *, delimiter: str) -> tuple[str, list[str]]:
    reader = csv.reader(io.StringIO(_read_text(path)), delimiter=delimiter)
    try:
        columns = [c.strip() or f"col{i + 1}" for i, c in enumerate(next(reader))]
    except StopIteration:
        return "", ["the file is empty"]
    rows = [list(r) for r in reader if any(str(c).strip() for c in r)]
    return _render_rows(title, columns, rows)


def _from_xlsx(path: Path, title: str) -> tuple[str, list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DatasetExtractionError(
            "reading .xlsx needs the openpyxl package"
        ) from exc

    # read_only + data_only: stream the cells, and take the cached result of a
    # formula rather than the formula text, which is not what anyone asked
    # about.
    book = load_workbook(path, read_only=True, data_only=True)
    chunks: list[str] = []
    notes: list[str] = []
    try:
        for sheet in book.worksheets:
            rows = [list(r) for r in sheet.iter_rows(values_only=True)]
            rows = [r for r in rows if any(c is not None and str(c).strip() for c in r)]
            if not rows:
                continue
            columns = [
                (str(c).strip() if c is not None and str(c).strip() else f"col{i + 1}")
                for i, c in enumerate(rows[0])
            ]
            sheet_title = f"{title} :: {sheet.title}" if len(book.worksheets) > 1 else title
            text, sheet_notes = _render_rows(sheet_title, columns, rows[1:])
            chunks.append(text)
            notes.extend(sheet_notes)
    finally:
        book.close()
    if not chunks:
        return "", ["the workbook has no non-empty sheets"]
    return "\n\n".join(chunks), notes


def _from_json(path: Path, title: str) -> tuple[str, list[str]]:
    """A JSON array of flat objects is a table; anything else is pretty text."""
    raw = _read_text(path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DatasetExtractionError(f"invalid JSON in {title}: {exc}") from exc

    if (
        isinstance(data, list)
        and data
        and all(isinstance(item, dict) for item in data)
        and all(not isinstance(v, (dict, list)) for item in data for v in item.values())
    ):
        columns: list[str] = []
        for item in data:
            for key in item:
                if key not in columns:
                    columns.append(key)
        rows = [[item.get(c) for c in columns] for item in data]
        return _render_rows(title, columns, rows)

    return json.dumps(data, indent=2, ensure_ascii=False), []


# ------------------------------------------------------------------------ docx
# A .docx is a zip whose body is one XML part. Reading it with the standard
# library keeps the dependency list as it is: a document-text extractor is not
# worth a new package, and the two element names below are stable WordprocessingML
# (ECMA-376) and have not moved in the format's lifetime.
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_DOCX_XML_BYTES = 64 * 1024 * 1024


def _from_docx(path: Path) -> str:
    """Paragraph text from a .docx, one paragraph per line.

    Headings come through as their plain text, which is exactly what the
    heading-aware chunker needs least and the breadcrumb needs most; a Word
    heading carries its level in a style attribute, not in the text, so no
    Markdown ``#`` is invented here. Splitting then falls back to windowing,
    which is honest about not knowing the structure.
    """
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(path) as archive:
            # Checked before reading: a zip bomb declares its uncompressed size
            # honestly, and a 25 MB upload that expands to gigabytes of XML
            # would otherwise be decompressed into memory before anyone looked.
            declared = archive.getinfo("word/document.xml").file_size
            if declared > _MAX_DOCX_XML_BYTES:
                raise DatasetExtractionError(
                    f"{path.name} expands to {declared / 1e6:.0f} MB of document XML, "
                    f"over the {_MAX_DOCX_XML_BYTES / 1e6:.0f} MB limit"
                )
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise DatasetExtractionError(
            f"{path.name} is not a readable .docx (no word/document.xml)"
        ) from exc

    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for para in root.iter(f"{_W_NS}p"):
        runs = [node.text or "" for node in para.iter(f"{_W_NS}t")]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


# ------------------------------------------------------------------------- pdf
def _from_pdf(path: Path) -> tuple[str, list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DatasetExtractionError("reading .pdf needs the pypdf package") from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    empty = 0
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            text = ""
        if text.strip():
            # A page marker doubles as a chunk boundary hint and tells the
            # reader of a citation where in the PDF the snippet came from.
            pages.append(f"## Page {i}\n\n{text.strip()}")
        else:
            empty += 1

    notes: list[str] = []
    if empty:
        notes.append(
            f"{empty} of {len(reader.pages)} pages had no extractable text "
            f"(likely scanned images; this build does not run OCR)"
        )
    return "\n\n".join(pages), notes
