"""Tests for units on hand warehouse queries and attachment Q&A."""

from __future__ import annotations

from app.engine.attachments import AttachmentInput
from app.engine.engine import InsightEngine
from app.providers.fake import FakeProvider


def test_units_on_hand_product_18_july_8():
    provider = FakeProvider()
    engine = InsightEngine.fixture(provider=provider, today="2026-07-15")
    res = engine.ask("what are the units on hand of product number 18 on 8th july")
    assert not res.abstained
    assert res.route == "structured"
    assert "product 18" in res.answer
    assert "1,933" in res.answer or "1933" in res.answer
    assert len(res.sql) >= 1
    assert "dim_product" in res.sql[0]


def test_units_on_hand_product_4_explicit_date():
    provider = FakeProvider()
    engine = InsightEngine.fixture(provider=provider, today="2026-07-15")
    res = engine.ask("7/1/2025 product number 4 units on hands")
    assert not res.abstained
    assert res.route == "structured"
    assert "product 4" in res.answer
    assert "2,008" in res.answer or "2008" in res.answer
    assert len(res.sql) >= 1
    assert "dim_product" in res.sql[0]


def test_order_items_return_reason_attachment():
    provider = FakeProvider()
    engine = InsightEngine.fixture(provider=provider, today="2026-07-15")
    csv_sample = (
        "order_item_id,order_id,product_id,quantity,unit_price,discount_amount,is_returned,return_reason\n"
        "15,7,2,1,292.64,0,FALSE,\n"
        "16,8,2,1,292.64,0,TRUE,wrong size\n"
        "17,9,2,1,292.64,0,FALSE,\n"
    )
    att = AttachmentInput(name="order_items.csv", content=csv_sample, content_type="text/csv")
    res = engine.ask("what is the return reason for order id 8", attachments=[att])
    assert not res.abstained
    assert res.route == "unstructured"
    assert "wrong size" in res.answer.lower()
    assert len(res.citations) >= 1
    assert any("order_items.csv" in c.title for c in res.citations)
    assert len(res.tables) >= 1
    assert "Attached" in res.tables[0].title
