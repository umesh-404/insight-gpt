"""Periodic inventory snapshots (grain: SKU x store x day).

Snapshots are taken on a fixed cadence (weekly by default) to keep the dataset
light; the fact grain still supports daily. The planted dip shows up here as a
modeled stockout: during 2026 Q2, Electronics SKUs in North stores run very low
on hand with elevated lead times, so ``days_of_inventory`` spikes and the
"which products should we restock?" question points at the same SKUs the sales
dip and the documents describe (``docs/02-data-model.md`` §8).
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from .config import GeneratorConfig
from .entities import Product, Store


@dataclass(frozen=True)
class InventorySnapshot:
    snapshot_date: str
    product_id: int
    store_id: int
    units_on_hand: int
    units_reserved: int
    reorder_point: int
    lead_time_days: int


def build_inventory(
    cfg: GeneratorConfig,
    rng: random.Random,
    products: list[Product],
    stores: list[Store],
    cadence_days: int = 7,
) -> list[InventorySnapshot]:
    snapshots: list[InventorySnapshot] = []
    start, end = cfg.start_date, cfg.end_date
    n_days = (end - start).days + 1
    for day_offset in range(0, n_days, cadence_days):
        day = start + dt.timedelta(days=day_offset)
        q = (day.month - 1) // 3 + 1
        in_dip_quarter = day.year == cfg.dip_year and q == cfg.dip_quarter
        for store in stores:
            for product in products:
                stocked_out = (
                    in_dip_quarter
                    and store.region == cfg.dip_region
                    and product.category == cfg.dip_category
                )
                if stocked_out:
                    on_hand = rng.randint(0, 30)
                    reserved = rng.randint(0, 5)
                    lead_time = rng.randint(28, 45)
                    reorder_point = 60
                else:
                    on_hand = rng.randint(120, 420)
                    reserved = rng.randint(0, 30)
                    lead_time = rng.randint(7, 24)
                    reorder_point = rng.randint(40, 80)
                snapshots.append(
                    InventorySnapshot(
                        snapshot_date=day.isoformat(),
                        product_id=product.product_id,
                        store_id=store.store_id,
                        units_on_hand=on_hand,
                        units_reserved=reserved,
                        reorder_point=reorder_point,
                        lead_time_days=lead_time,
                    )
                )
    return snapshots
