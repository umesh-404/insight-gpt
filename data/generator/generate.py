"""Top-level generation orchestrator.

Builds every entity from a single seed and writes the operational source files
to ``data/generated/`` (CSV, optional parquet) and documents to
``data/generated/documents/*.json``. Returns a summary of what was written.

Determinism: each phase gets its own ``random.Random`` derived from the base
seed, so the whole dataset is reproducible and one phase's volume never shifts
another phase's stream.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .config import GeneratorConfig
from .documents import build_documents, document_to_dict
from .entities import build_customers, build_products, build_stores
from .inventory import build_inventory
from .io_writers import write_csv, write_json, write_parquet
from .orders import build_transactions

# Column orders mirror docs/02-data-model.md §2 operational schemas.
CUSTOMER_COLUMNS = [
    "customer_id", "email", "full_name", "phone",
    "region", "country", "segment", "signup_date",
]
PRODUCT_COLUMNS = [
    "product_id", "sku", "product_name", "category", "subcategory",
    "brand", "unit_cost", "list_price", "active",
]
STORE_COLUMNS = ["store_id", "store_name", "channel", "region", "opened_date"]
ORDER_COLUMNS = [
    "order_id", "customer_id", "store_id", "order_ts", "status",
    "currency", "discount_amount", "shipping_amount",
]
ORDER_ITEM_COLUMNS = [
    "order_item_id", "order_id", "product_id", "quantity", "unit_price",
    "discount_amount", "is_returned", "return_reason",
]
INVENTORY_COLUMNS = [
    "snapshot_date", "product_id", "store_id", "units_on_hand",
    "units_reserved", "reorder_point", "lead_time_days",
]

_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "generated"


@dataclass(frozen=True)
class GenerationResult:
    out_dir: Path
    counts: dict[str, int]
    parquet_written: bool


def generate(cfg: GeneratorConfig | None = None, out_dir: Path | None = None) -> GenerationResult:
    cfg = cfg or GeneratorConfig()
    out = Path(out_dir) if out_dir else _DEFAULT_OUT

    customers = build_customers(cfg, random.Random(cfg.seed + 1))
    products = build_products(cfg, random.Random(cfg.seed + 2))
    stores = build_stores(cfg, random.Random(cfg.seed + 3))
    txns = build_transactions(
        cfg, random.Random(cfg.seed + 4), customers, products, stores
    )
    inventory = build_inventory(cfg, random.Random(cfg.seed + 5), products, stores)
    documents = build_documents(cfg, random.Random(cfg.seed + 6), customers, products)

    tables = [
        ("customers", customers, CUSTOMER_COLUMNS),
        ("products", products, PRODUCT_COLUMNS),
        ("stores", stores, STORE_COLUMNS),
        ("orders", txns.orders, ORDER_COLUMNS),
        ("order_items", txns.order_items, ORDER_ITEM_COLUMNS),
        ("inventory", inventory, INVENTORY_COLUMNS),
    ]

    counts: dict[str, int] = {}
    parquet_written = False
    for name, records, columns in tables:
        counts[name] = write_csv(out / f"{name}.csv", records, columns)
        if cfg.write_parquet:
            parquet_written = write_parquet(
                out / f"{name}.parquet", records, columns
            ) or parquet_written

    # Documents split by type into JSON arrays for the document connector.
    doc_dicts = [document_to_dict(d) for d in documents]
    by_type: dict[str, list[dict]] = {"ticket": [], "review": [], "report": []}
    for d in doc_dicts:
        by_type[d["doc_type"]].append(d)
    file_for = {
        "ticket": "support_tickets.json",
        "review": "reviews.json",
        "report": "reports.json",
    }
    for doc_type, items in by_type.items():
        counts[file_for[doc_type]] = write_json(
            out / "documents" / file_for[doc_type], items
        )

    return GenerationResult(out_dir=out, counts=counts, parquet_written=parquet_written)
