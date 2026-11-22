"""Uploaded file -> text, per format.

The point of these tests is that the text a format produces is *retrievable*:
a row's values must sit next to their column names, a PDF page must say which
page it was, and a file that yields nothing must say so rather than produce a
plausible-looking empty success.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from retrieval.loaders import (
    MAX_TABULAR_ROWS,
    SUPPORTED_EXTENSIONS,
    DatasetExtractionError,
    UnsupportedDatasetFile,
    classify,
    extract_text,
)


def test_classify_splits_tabular_from_document():
    assert classify("a.csv") == "tabular"
    assert classify("a.XLSX") == "tabular"
    assert classify("a.pdf") == "document"
    assert classify("a.md") == "document"


def test_an_unknown_extension_names_what_is_accepted():
    with pytest.raises(UnsupportedDatasetFile) as excinfo:
        classify("malware.exe")
    message = str(excinfo.value)
    for ext in SUPPORTED_EXTENSIONS:
        assert ext in message


def test_csv_rows_carry_their_column_names(tmp_path):
    """``1899`` on its own embeds nothing. ``revenue: 1899`` embeds a fact."""
    f = tmp_path / "sales.csv"
    f.write_text(
        "date,sku,region,revenue\n"
        "2026-04-02,X230,North,1899\n"
        "2026-04-03,A11,South,250\n",
        encoding="utf-8",
    )
    text, kind, notes = extract_text(f)
    assert kind == "tabular"
    assert notes == []
    assert "Columns (4): date, sku, region, revenue" in text
    assert "region: North" in text and "revenue: 1899" in text
    # One line per row, so the chunker's line windows never split a record.
    assert text.count("Row ") == 2


def test_tsv_uses_tabs(tmp_path):
    f = tmp_path / "a.tsv"
    f.write_text("a\tb\n1\t2\n", encoding="utf-8")
    text, _, _ = extract_text(f)
    assert "a: 1 | b: 2" in text


def test_an_empty_csv_says_so_rather_than_inventing_content(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("", encoding="utf-8")
    text, _, notes = extract_text(f)
    assert text == ""
    assert notes == ["the file is empty"]


def test_a_row_cap_is_reported_not_silent(tmp_path):
    f = tmp_path / "big.csv"
    rows = "\n".join(f"{i},x" for i in range(MAX_TABULAR_ROWS + 10))
    f.write_text(f"id,label\n{rows}\n", encoding="utf-8")
    text, _, notes = extract_text(f)
    assert text.count("Row ") == MAX_TABULAR_ROWS
    assert notes and "not retrievable" in notes[0]


def test_a_json_array_of_flat_objects_is_treated_as_a_table(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps([{"sku": "X230", "qty": 4}, {"sku": "A11", "qty": 1}]),
                 encoding="utf-8")
    text, kind, _ = extract_text(f)
    assert kind == "document"  # by extension; the RENDERING is tabular
    assert "sku: X230 | qty: 4" in text


def test_nested_json_is_kept_as_readable_text(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"config": {"region": "North"}}), encoding="utf-8")
    text, _, _ = extract_text(f)
    assert '"region": "North"' in text


def test_invalid_json_is_a_clean_failure(tmp_path):
    f = tmp_path / "a.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetExtractionError):
        extract_text(f)


def test_a_mislabelled_encoding_does_not_fail_the_upload(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes("café costs 300".encode("latin-1"))
    text, _, _ = extract_text(f)
    assert "costs" in text  # decoded, not raised


def test_a_missing_file_is_a_clean_failure(tmp_path):
    with pytest.raises(DatasetExtractionError):
        extract_text(tmp_path / "nope.csv")


def test_docx_paragraphs_come_through(tmp_path):
    """Built by hand: a .docx is a zip with one XML part, read with stdlib."""
    body = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Quarterly review</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>North </w:t></w:r><w:r><w:t>fulfilment lagged.</w:t></w:r></w:p>"
        "<w:p/>"
        "</w:body></w:document>"
    )
    f = tmp_path / "review.docx"
    with zipfile.ZipFile(f, "w") as archive:
        archive.writestr("word/document.xml", body)

    text, kind, _ = extract_text(f)
    assert kind == "document"
    assert "Quarterly review" in text
    # Runs within one paragraph are joined, so a word split across runs by
    # Word's own formatting is not split in the indexed text.
    assert "North fulfilment lagged." in text


def test_a_docx_that_is_not_a_docx_fails_clearly(tmp_path):
    f = tmp_path / "fake.docx"
    f.write_bytes(b"not a zip at all")
    with pytest.raises(DatasetExtractionError):
        extract_text(f)


def test_xlsx_rows_carry_their_column_names(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["sku", "qty"])
    sheet.append(["X230", 4])
    f = tmp_path / "stock.xlsx"
    book.save(f)

    text, kind, _ = extract_text(f)
    assert kind == "tabular"
    assert "sku: X230 | qty: 4" in text
