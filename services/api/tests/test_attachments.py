"""Tests for uploaded spreadsheet and document processing."""

import io
import openpyxl
from app.engine.engine import InsightEngine


def test_uploaded_excel_processing():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Revenue", "Units"])
    ws.append(["North", 500000.0, 100])
    ws.append(["South", 300000.0, 60])
    ws.append(["East", 200000.0, 40])

    buf = io.BytesIO()
    wb.save(buf)

    engine = InsightEngine.fixture()
    attachments = [{"name": "sales_q3.xlsx", "kind": "document", "raw_bytes": buf.getvalue()}]

    # Query total revenue
    env = engine.ask("What is the total revenue in this excel sheet?", attachments=attachments)
    assert env.route == "structured"
    assert "1,000,000" in env.answer or "1000000" in env.answer
    assert env.sql
    assert env.tables
    assert any("sales_q3.xlsx" in c.title for c in env.citations)


def test_uploaded_document_processing():
    doc_text = """
Company Travel Policy 2026:
- Maximum hotel allowance is $250 per night.
- Meal per diem is $75 per day.
- Flight bookings must be made at least 14 days in advance.
    """.strip()

    engine = InsightEngine.fixture()
    attachments = [{"name": "travel_policy.txt", "kind": "document", "raw_bytes": doc_text.encode("utf-8")}]

    env = engine.ask("What is the maximum hotel allowance?", attachments=attachments)
    assert env.route == "unstructured"
    assert "$250" in env.answer
    assert any("travel_policy.txt" in c.title for c in env.citations)
    assert "north" not in env.answer.lower()


def test_uploaded_json_reports_summary():
    from pathlib import Path

    reports_path = Path("d:/capstone/insight-gpt/data/generated/documents/reports.json")
    if not reports_path.exists():
        return
    reports_bytes = reports_path.read_bytes()

    engine = InsightEngine.fixture()
    attachments = [{"name": "reports.json", "kind": "document", "raw_bytes": reports_bytes}]

    env = engine.ask("summarize these files", attachments=attachments)
    assert "*[ *:" not in env.answer
    assert "2025Q3 performance summary" in env.answer
    assert "2026Q2 operations review" in env.answer
    assert len(env.citations) >= 4


def test_multi_file_attachments():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Category", "Sales"])
    ws.append(["Hardware", 5000.0])
    buf = io.BytesIO()
    wb.save(buf)

    doc_text = "Vendor contract notes: SLA response time is 1 hour."

    engine = InsightEngine.fixture()
    attachments = [
        {"name": "sales.xlsx", "kind": "document", "raw_bytes": buf.getvalue()},
        {"name": "contract.txt", "kind": "document", "raw_bytes": doc_text.encode("utf-8")},
    ]

    env = engine.ask("summarize these files", attachments=attachments)
    assert "sales.xlsx" in env.answer
    assert "contract.txt" in env.answer
    assert len(env.citations) >= 2


def test_distributor_price_list_excel():
    """Test real-world distributor price list with banner headers and empty leading columns."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Banner metadata rows
    ws.cell(row=1, column=14, value="MURUGAN FOOD PRODUCTS")
    ws.cell(row=2, column=14, value="#6/289, Temple Street, Madurai - 625001")
    ws.cell(row=3, column=14, value="DISTRIBUTOR PRICE LIST")

    # Table header at row 4
    headers = [
        "S. No.", "Description", "Wht GMS", "Case Qty",
        "DB Invoice Price", "DB Margin 10%", "Retailer Price", "MRP", "DB Case invoice price"
    ]
    for col_idx, h in enumerate(headers, start=14):
        ws.cell(row=4, column=col_idx, value=h)

    # Data rows
    data = [
        [1, "Sai Murugan Plain Papad", 70, 250, 11.36, 1.14, 12.5, 25.0, 2840.0],
        [2, "Sai Murugan Plain Papad", 100, 200, 15.0, 1.50, 16.5, 35.0, 3000.0],
        [3, "Sai Murugan Plain Papad", 150, 135, 22.5, 2.25, 24.75, 50.0, 3037.5],
    ]
    for r_offset, row_vals in enumerate(data, start=5):
        for c_offset, val in enumerate(row_vals, start=14):
            ws.cell(row=r_offset, column=c_offset, value=val)

    buf = io.BytesIO()
    wb.save(buf)

    engine = InsightEngine.fixture()
    attachments = [{"name": "ap distributor.xlsx", "kind": "document", "raw_bytes": buf.getvalue()}]

    env = engine.ask("list all prices", attachments=attachments)
    assert env.route == "structured"
    assert "Sai Murugan Plain Papad" in env.answer
    assert "₹11.36" in env.answer
    assert "₹25.00" in env.answer
    assert "Pricing Highlights:" in env.answer
    assert env.tables
    assert len(env.tables[0].columns) == 9
    assert env.sql
    assert "Column_1" not in env.answer


def test_attachment_followup_highest_price_and_item_filter():
    """Verify follow-up queries like 'which has highest price' and item/weight filter queries."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    headers = [
        "S. No.", "Description", "Wht GMS", "Case Qty",
        "DB Invoice Price", "DB Margin 10%", "Retailer Price", "MRP", "DB Case invoice price"
    ]
    for col_idx, h in enumerate(headers, start=1):
        ws.cell(row=1, column=col_idx, value=h)

    data = [
        [1, "Sai Murugan Plain Papad (70g)", 70, 250, 11.36, 1.14, 12.5, 25.0, 2840.0],
        [2, "Sai Murugan Plain Papad (100g)", 100, 200, 15.0, 1.50, 16.5, 35.0, 3000.0],
        [3, "Sai Murugan Plain Papad (150g)", 150, 135, 22.5, 2.25, 24.75, 50.0, 3037.5],
    ]
    for r_offset, row_vals in enumerate(data, start=2):
        for c_offset, val in enumerate(row_vals, start=1):
            ws.cell(row=r_offset, column=c_offset, value=val)

    buf = io.BytesIO()
    wb.save(buf)

    engine = InsightEngine.fixture()
    attachments = [{"name": "ap distributor.xlsx", "kind": "document", "raw_bytes": buf.getvalue()}]

    # Query 1: which has highest price?
    env_max = engine.ask("which has highest price?", attachments=attachments)
    assert env_max.route == "structured"
    assert "50" in env_max.answer or "₹50.00" in env_max.answer
    assert "150g" in env_max.answer or "Sai Murugan Plain Papad" in env_max.answer

    # Query 2: price of 100g
    env_filter = engine.ask("what is the price of 100g?", attachments=attachments)
    assert env_filter.route == "structured"
    assert "100" in env_filter.answer
    assert "35" in env_filter.answer or "15" in env_filter.answer


def test_conversation_attachment_context_memory():
    """Verify that attachments uploaded in Turn 1 are remembered in Turn 2 across conversation store."""
    from app.api.conversations import (
        append_turn,
        get_conversation_attachments,
        get_conversation_history,
    )
    from app.engine.envelope import AnswerEnvelope, Citation

    user_id = "test-user-123"
    conv_id = "conv-multi-turn-001"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Item", "MRP"])
    ws.append(["Premium Widget", 99.0])
    buf = io.BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()

    # Turn 1: Attach spreadsheet and ask question
    att_turn1 = [{"name": "catalog.xlsx", "kind": "document", "raw_bytes": file_bytes}]
    append_turn(
        user_id=user_id,
        conversation_id=conv_id,
        question="list all items",
        message_id="msg-turn-1",
        envelope=AnswerEnvelope(
            answer="Premium Widget: ₹99.00",
            route="structured",
            citations=[Citation(n=1, doc_id="cat1", source_type="excel", title="catalog.xlsx")],
        ),
        attachments=att_turn1,
    )

    # Verify conversation remembered the attachment
    persisted_atts = get_conversation_attachments(user_id=user_id, conversation_id=conv_id)
    assert len(persisted_atts) == 1
    assert persisted_atts[0]["name"] == "catalog.xlsx"
    assert persisted_atts[0]["raw_bytes"] == file_bytes

    # Turn 2: User asks follow-up with NO attachments sent in this turn
    turn2_atts = []
    if not turn2_atts and conv_id:
        turn2_atts = get_conversation_attachments(user_id=user_id, conversation_id=conv_id)

    assert len(turn2_atts) == 1

    engine = InsightEngine.fixture()
    history = get_conversation_history(user_id=user_id, conversation_id=conv_id)
    env2 = engine.ask("what is the price of Premium Widget?", attachments=turn2_atts, history=history)
    assert env2.route == "structured"
    assert "99" in env2.answer


