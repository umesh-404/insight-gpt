"""Transactional generation: orders + order lines, demand-driven per day.

Generation is driven by an expected-lines grid per ``(day, region, category)``
so revenue by region and category is directly controllable — that is what makes
the planted 2026 Q2 North x Electronics dip land precisely (``docs/02`` §8) while
the rest of the dataset only softens mildly. Lines are then bundled into orders
of 1–3 lines from one customer + store in the region, keeping the star schema
joinable and the order grain realistic.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from .config import BASE_DAILY_LINES, CATEGORIES, REGIONS, GeneratorConfig
from .entities import Customer, Product, Store
from .seasonality import seasonality

_ORDER_STATUSES = ["placed", "shipped", "delivered", "cancelled", "returned"]

_RETURN_REASONS_GENERIC = [
    "changed mind",
    "wrong size",
    "found better price",
    "no longer needed",
    "damaged in transit",
]
_RETURN_REASONS_DIP = [
    "arrived too late",
    "cancelled due to delivery delay",
    "shipment delayed, refund requested",
    "fulfilment backlog, order abandoned",
]


@dataclass(frozen=True)
class Order:
    order_id: int
    customer_id: int
    store_id: int
    order_ts: str
    status: str
    currency: str
    discount_amount: float
    shipping_amount: float


@dataclass(frozen=True)
class OrderItem:
    order_item_id: int
    order_id: int
    product_id: int
    quantity: int
    unit_price: float
    discount_amount: float
    is_returned: bool
    return_reason: str | None


@dataclass(frozen=True)
class Transactions:
    orders: list[Order]
    order_items: list[OrderItem]
    high_return_product_ids: set[int]


def _sample_count(mean: float, rng: random.Random) -> int:
    """Round an expected count to an integer with a little seeded noise."""
    if mean <= 0:
        return 0
    noisy = mean * rng.uniform(0.8, 1.2)
    whole = int(noisy)
    frac = noisy - whole
    return whole + (1 if rng.random() < frac else 0)


def _index_by(products: list[Product]) -> dict[str, list[Product]]:
    by_cat: dict[str, list[Product]] = {c: [] for c in CATEGORIES}
    for p in products:
        by_cat[p.category].append(p)
    return by_cat


def build_transactions(
    cfg: GeneratorConfig,
    rng: random.Random,
    customers: list[Customer],
    products: list[Product],
    stores: list[Store],
) -> Transactions:
    cust_by_region: dict[str, list[Customer]] = {r: [] for r in REGIONS}
    for c in customers:
        cust_by_region[c.region].append(c)
    store_by_region: dict[str, list[Store]] = {r: [] for r in REGIONS}
    for s in stores:
        store_by_region[s.region].append(s)
    products_by_cat = _index_by(products)

    # Deterministically flag two Electronics SKUs as high-return.
    electronics = sorted(products_by_cat["Electronics"], key=lambda p: p.product_id)
    high_return_ids = {p.product_id for p in electronics[:2]}

    orders: list[Order] = []
    order_items: list[OrderItem] = []
    order_id = 1
    item_id = 1

    start, end = cfg.start_date, cfg.end_date
    n_days = (end - start).days + 1
    for day_offset in range(n_days):
        day = start + dt.timedelta(days=day_offset)
        season = seasonality(day)
        for region in REGIONS:
            region_customers = cust_by_region[region]
            region_stores = store_by_region[region]
            if not region_customers or not region_stores:
                continue
            # Expand the per-category expected lines into a flat request list.
            line_requests: list[str] = []
            for category in CATEGORIES:
                mean = (
                    BASE_DAILY_LINES[(region, category)]
                    * cfg.volume_scale
                    * season
                    * cfg.dip_multiplier(region, category, day)
                )
                line_requests.extend([category] * _sample_count(mean, rng))
            if not line_requests:
                continue
            rng.shuffle(line_requests)

            # Bundle lines into orders of 1–3 lines from one customer + store.
            i = 0
            while i < len(line_requests):
                bundle = line_requests[i : i + rng.randint(1, 3)]
                i += len(bundle)
                customer = rng.choice(region_customers)
                store = rng.choice(region_stores)
                ts = dt.datetime.combine(
                    day, dt.time(rng.randint(6, 22), rng.randint(0, 59), rng.randint(0, 59))
                )
                any_returned = False
                for category in bundle:
                    product = rng.choice(products_by_cat[category])
                    qty = _quantity_for(category, rng)
                    unit_price = round(product.list_price, 2)
                    discount = _line_discount(cfg, unit_price, qty, rng)
                    returned, reason = _return_decision(
                        cfg, region, category, product.product_id, high_return_ids, day, rng
                    )
                    any_returned = any_returned or returned
                    order_items.append(
                        OrderItem(
                            order_item_id=item_id,
                            order_id=order_id,
                            product_id=product.product_id,
                            quantity=qty,
                            unit_price=unit_price,
                            discount_amount=discount,
                            is_returned=returned,
                            return_reason=reason,
                        )
                    )
                    item_id += 1
                status = _order_status(cfg, region, bundle, any_returned, day, rng)
                orders.append(
                    Order(
                        order_id=order_id,
                        customer_id=customer.customer_id,
                        store_id=store.store_id,
                        order_ts=ts.isoformat(sep=" "),
                        status=status,
                        currency=cfg.currency,
                        discount_amount=0.0,
                        shipping_amount=round(rng.uniform(0, 12), 2),
                    )
                )
                order_id += 1

    return Transactions(
        orders=orders,
        order_items=order_items,
        high_return_product_ids=high_return_ids,
    )


def _quantity_for(category: str, rng: random.Random) -> int:
    # Big-ticket electronics sell in ones/twos; consumables a bit more.
    if category == "Electronics":
        return rng.choices([1, 2, 3], weights=[0.75, 0.2, 0.05])[0]
    return rng.choices([1, 2, 3, 4], weights=[0.5, 0.3, 0.15, 0.05])[0]


def _line_discount(
    cfg: GeneratorConfig, unit_price: float, qty: int, rng: random.Random
) -> float:
    if rng.random() >= cfg.discount_line_fraction:
        return 0.0
    gross = unit_price * qty
    pct = rng.uniform(0.05, 0.25)
    # Cap below gross so net revenue can never go negative (singular test).
    return round(min(gross * pct, gross * 0.9), 2)


def _return_decision(
    cfg: GeneratorConfig,
    region: str,
    category: str,
    product_id: int,
    high_return_ids: set[int],
    day: dt.date,
    rng: random.Random,
) -> tuple[bool, str | None]:
    rate = cfg.base_return_rate
    if product_id in high_return_ids:
        rate = max(rate, cfg.high_return_rate)
    in_dip = cfg.in_dip(region, category, day)
    if in_dip:
        rate = max(rate, cfg.dip_return_rate)
    if rng.random() >= rate:
        return False, None
    reason = rng.choice(_RETURN_REASONS_DIP if in_dip else _RETURN_REASONS_GENERIC)
    return True, reason


def _order_status(
    cfg: GeneratorConfig,
    region: str,
    bundle: list[str],
    any_returned: bool,
    day: dt.date,
    rng: random.Random,
) -> str:
    if any_returned:
        return "returned"
    in_dip = any(cfg.in_dip(region, c, day) for c in bundle)
    if in_dip:
        # Backlog quarter: more cancellations and in-flight (not delivered) orders.
        return rng.choices(
            ["delivered", "shipped", "placed", "cancelled"],
            weights=[0.45, 0.2, 0.1, 0.25],
        )[0]
    return rng.choices(
        ["delivered", "shipped", "placed", "cancelled"],
        weights=[0.82, 0.08, 0.05, 0.05],
    )[0]
