"""Offline tests for the synthetic data generator.

These run with no database and no dbt. They assert the two properties the rest of
the platform depends on: the **planted dip** is really present in the generated
facts (so "why did sales decline last quarter?" has a real answer), and the CSVs
are **referentially intact** and clean (the shape dbt staging expects). They also
pin **determinism** — the whole point of a seedable generator.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

import pytest

from data.generator import GeneratorConfig, generate


def _load(out: Path, name: str) -> list[dict]:
    with (out / f"{name}.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _quarter(ts: str) -> str:
    d = dt.date.fromisoformat(ts[:10])
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated")
    generate(GeneratorConfig(seed=7), out_dir=out)
    return out


def _revenue_by_region_category(out: Path) -> dict[tuple[str, str, str], float]:
    customers = {c["customer_id"]: c for c in _load(out, "customers")}
    products = {p["product_id"]: p for p in _load(out, "products")}
    orders = {o["order_id"]: o for o in _load(out, "orders")}
    rev: dict[tuple[str, str, str], float] = defaultdict(float)
    for it in _load(out, "order_items"):
        order = orders[it["order_id"]]
        region = customers[order["customer_id"]]["region"]
        category = products[it["product_id"]]["category"]
        net = float(it["unit_price"]) * int(it["quantity"]) - float(it["discount_amount"])
        rev[(_quarter(order["order_ts"]), region, category)] += net
    return rev


def test_planted_dip_north_electronics(dataset: Path) -> None:
    rev = _revenue_by_region_category(dataset)
    q1 = rev[("2026Q1", "North", "Electronics")]
    q2 = rev[("2026Q2", "North", "Electronics")]
    assert q1 > 0 and q2 > 0
    # The planted decline: Q2 well below Q1 (dip factor ~0.45, allow slack).
    assert q2 / q1 < 0.6, f"expected a material dip, got ratio {q2 / q1:.3f}"


def test_dip_is_concentrated_not_global(dataset: Path) -> None:
    """Other North categories and other regions' Electronics must NOT collapse,
    so the decline is attributable to North x Electronics specifically."""
    rev = _revenue_by_region_category(dataset)
    north_elec = (
        rev[("2026Q2", "North", "Electronics")] / rev[("2026Q1", "North", "Electronics")]
    )
    for category in ("Apparel", "Home"):
        ratio = rev[("2026Q2", "North", category)] / rev[("2026Q1", "North", category)]
        assert ratio > north_elec, f"North {category} dipped as hard as Electronics"
    for region in ("South", "East", "West"):
        ratio = (
            rev[("2026Q2", region, "Electronics")]
            / rev[("2026Q1", region, "Electronics")]
        )
        assert ratio > north_elec, f"{region} Electronics dipped as hard as North"


def test_referential_integrity(dataset: Path) -> None:
    customer_ids = {c["customer_id"] for c in _load(dataset, "customers")}
    product_ids = {p["product_id"] for p in _load(dataset, "products")}
    store_ids = {s["store_id"] for s in _load(dataset, "stores")}
    orders = _load(dataset, "orders")
    order_ids = {o["order_id"] for o in orders}

    for o in orders:
        assert o["customer_id"] in customer_ids
        assert o["store_id"] in store_ids
    for it in _load(dataset, "order_items"):
        assert it["order_id"] in order_ids
        assert it["product_id"] in product_ids
    for inv in _load(dataset, "inventory"):
        assert inv["product_id"] in product_ids
        assert inv["store_id"] in store_ids


def test_quality_invariants(dataset: Path) -> None:
    today = dt.date(2026, 7, 15)
    valid_status = {"placed", "shipped", "delivered", "cancelled", "returned"}
    for o in _load(dataset, "orders"):
        assert o["status"] in valid_status
        assert dt.date.fromisoformat(o["order_ts"][:10]) <= today  # no future orders
    for it in _load(dataset, "order_items"):
        net = float(it["unit_price"]) * int(it["quantity"]) - float(it["discount_amount"])
        assert net >= 0  # revenue non-negative
        assert it["is_returned"] in {"True", "False"}


def test_returned_units_le_sold(dataset: Path) -> None:
    sold = returned = 0
    for it in _load(dataset, "order_items"):
        qty = int(it["quantity"])
        sold += qty
        if it["is_returned"] == "True":
            returned += qty
    assert 0 < returned <= sold


def test_inventory_stockout_in_dip(dataset: Path) -> None:
    """North Electronics inventory in Q2 runs far lower than the rest — the
    modeled cause behind the sales dip and the restock question."""
    products = {p["product_id"]: p for p in _load(dataset, "products")}
    stores = {s["store_id"]: s for s in _load(dataset, "stores")}
    dip_levels: list[int] = []
    other_levels: list[int] = []
    for inv in _load(dataset, "inventory"):
        d = dt.date.fromisoformat(inv["snapshot_date"])
        q = f"{d.year}Q{(d.month - 1) // 3 + 1}"
        region = stores[inv["store_id"]]["region"]
        category = products[inv["product_id"]]["category"]
        target = dip_levels if (
            q == "2026Q2" and region == "North" and category == "Electronics"
        ) else other_levels
        target.append(int(inv["units_on_hand"]))
    avg_dip = sum(dip_levels) / len(dip_levels)
    avg_other = sum(other_levels) / len(other_levels)
    assert avg_dip < avg_other * 0.25, (avg_dip, avg_other)


def test_documents_echo_the_dip(dataset: Path) -> None:
    import json

    docs_dir = dataset / "documents"
    reports = json.loads((docs_dir / "reports.json").read_text(encoding="utf-8"))
    q2 = next(r for r in reports if r["period"] == "2026Q2")
    assert "North" in q2["body"] and "Electronics" in q2["body"]

    reviews = json.loads((docs_dir / "reviews.json").read_text(encoding="utf-8"))
    q2_north_neg = [
        r for r in reviews
        if r["region"] == "North" and r["category"] == "Electronics"
        and r["created_ts"].startswith("2026-0") and int(r["rating"]) <= 2
        and r["created_ts"][5:7] in {"04", "05", "06"}
    ]
    assert len(q2_north_neg) >= 5


def test_determinism_same_seed(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate(GeneratorConfig(seed=123), out_dir=a)
    generate(GeneratorConfig(seed=123), out_dir=b)
    for name in ("customers", "products", "stores", "orders", "order_items", "inventory"):
        assert (a / f"{name}.csv").read_bytes() == (b / f"{name}.csv").read_bytes()


def test_different_seed_differs(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate(GeneratorConfig(seed=1), out_dir=a)
    generate(GeneratorConfig(seed=2), out_dir=b)
    assert (a / "order_items.csv").read_bytes() != (b / "order_items.csv").read_bytes()


def test_documents_are_deterministic_too(tmp_path: Path) -> None:
    """The documents are half the demo; determinism has to cover them as well.

    The CSVs were already pinned, but the document JSONs are what the ingestion
    hand-off publishes and retrieval embeds — a non-deterministic corpus would
    make every re-seed look like the whole corpus changed and re-embed it.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    generate(GeneratorConfig(seed=123), out_dir=a)
    generate(GeneratorConfig(seed=123), out_dir=b)
    for name in ("support_tickets", "reviews", "reports"):
        assert (a / "documents" / f"{name}.json").read_bytes() == (
            b / "documents" / f"{name}.json"
        ).read_bytes()


def test_documents_carry_the_product_sku(dataset: Path) -> None:
    """`product_sku` becomes retrieval's `product_ref` and is the identifier a
    lexical/sparse query can match exactly — the integer id cannot."""
    import json

    skus = {p["sku"] for p in _load(dataset, "products")}
    for name in ("support_tickets", "reviews"):
        docs = json.loads((dataset / "documents" / f"{name}.json").read_text(encoding="utf-8"))
        assert docs, f"{name}.json is empty"
        for doc in docs:
            assert doc["product_sku"] in skus
            # ...and it is named in the text, not only in the metadata.
            assert doc["product_sku"] in doc["body"]


def test_reports_have_no_product(dataset: Path) -> None:
    import json

    reports = json.loads((dataset / "documents" / "reports.json").read_text(encoding="utf-8"))
    assert all(r["product_sku"] is None and r["product_id"] is None for r in reports)
