"""Dimension-like source entities: customers, products, stores.

These are the operational shapes (``docs/02-data-model.md`` §2.1). PII fields on
customers (email, full_name, phone) are generated as realistic-looking synthetic
values so the ingestion redaction step (``services/ingestion``) has something to
mask before landing in ``raw`` — the warehouse never sees them un-redacted.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import (
    CATEGORIES,
    CHANNELS,
    REGIONS,
    SEGMENTS,
    SUBCATEGORIES,
    GeneratorConfig,
)

_FIRST_NAMES = [
    "Ava", "Liam", "Noah", "Emma", "Olivia", "Mia", "Ethan", "Sophia", "Lucas",
    "Isabella", "Mason", "Amelia", "Leo", "Harper", "Ruby", "Kai", "Nora", "Owen",
    "Priya", "Arjun", "Wei", "Yuki", "Diego", "Fatima", "Omar", "Chloe", "Ivan",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Nguyen", "Patel", "Garcia", "Chen", "Kim", "Rossi",
    "Muller", "Silva", "Okafor", "Haddad", "Novak", "Costa", "Ivanov", "Tanaka",
    "Andersson", "Dubois", "Kowalski", "Fernandez",
]
_STREETS = ["Maple", "Oak", "Pine", "Cedar", "Elm", "Birch", "Willow", "Ash"]

# Invented brand names (no real trademarks), kept ASCII for stable CSV output.
_BRANDS = {
    "Electronics": ["Nimbus", "Voltra", "Corepad", "Pixona"],
    "Apparel": ["Northwind", "Loomly", "Trailhead", "Everweave"],
    "Home": ["Hearthly", "Castiron", "Rootwood", "Lumen"],
}

_PRODUCT_NOUNS = {
    "Laptops": ["UltraBook", "ProBook", "AirBook"],
    "Phones": ["Pulse", "Edge", "Nova"],
    "Audio": ["SoundPod", "BassBar", "ClearBuds"],
    "Accessories": ["ChargeKit", "SleeveCase", "DockHub"],
    "Tops": ["Cotton Tee", "Merino Henley", "Linen Shirt"],
    "Outerwear": ["Rain Shell", "Down Parka", "Field Jacket"],
    "Footwear": ["Trail Runner", "Court Sneaker", "Chelsea Boot"],
    "Kitchen": ["Chef Knife", "Saute Pan", "Kettle"],
    "Furniture": ["Oak Stool", "Desk Lamp", "Bookshelf"],
    "Decor": ["Wall Print", "Throw Blanket", "Vase Set"],
}


@dataclass(frozen=True)
class Customer:
    customer_id: int
    email: str
    full_name: str
    phone: str
    region: str
    country: str
    segment: str
    signup_date: str


@dataclass(frozen=True)
class Product:
    product_id: int
    sku: str
    product_name: str
    category: str
    subcategory: str
    brand: str
    unit_cost: float
    list_price: float
    active: bool


@dataclass(frozen=True)
class Store:
    store_id: int
    store_name: str
    channel: str
    region: str
    opened_date: str


def build_customers(cfg: GeneratorConfig, rng: random.Random) -> list[Customer]:
    customers: list[Customer] = []
    for cid in range(1, cfg.n_customers + 1):
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        region = rng.choice(REGIONS)
        segment = rng.choices(SEGMENTS, weights=[0.7, 0.2, 0.1])[0]
        # Synthetic-but-realistic PII (redacted at ingestion, never landed raw).
        email = f"{first.lower()}.{last.lower()}{cid}@example.com"
        phone = f"+1-{rng.randint(200, 989)}-{rng.randint(200, 999)}-{rng.randint(1000, 9999)}"
        year = rng.randint(2021, 2025)
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        customers.append(
            Customer(
                customer_id=cid,
                email=email,
                full_name=f"{first} {last}",
                phone=phone,
                region=region,
                country="US",
                segment=segment,
                signup_date=f"{year:04d}-{month:02d}-{day:02d}",
            )
        )
    return customers


def build_products(cfg: GeneratorConfig, rng: random.Random) -> list[Product]:
    products: list[Product] = []
    pid = 1
    for category in CATEGORIES:
        for subcategory in SUBCATEGORIES[category]:
            for _ in range(cfg.products_per_subcategory):
                brand = rng.choice(_BRANDS[category])
                noun = rng.choice(_PRODUCT_NOUNS[subcategory])
                variant = rng.choice(["", " 2", " Pro", " Lite", " Max"])
                name = f"{brand} {noun}{variant}".strip()
                # Price bands differ by category so margins are non-trivial.
                if category == "Electronics":
                    list_price = round(rng.uniform(120, 1600), 2)
                elif category == "Apparel":
                    list_price = round(rng.uniform(20, 220), 2)
                else:
                    list_price = round(rng.uniform(15, 400), 2)
                unit_cost = round(list_price * rng.uniform(0.45, 0.7), 2)
                sku = f"{category[:3].upper()}-{subcategory[:3].upper()}-{pid:04d}"
                products.append(
                    Product(
                        product_id=pid,
                        sku=sku,
                        product_name=name,
                        category=category,
                        subcategory=subcategory,
                        brand=brand,
                        unit_cost=unit_cost,
                        list_price=list_price,
                        active=rng.random() > 0.05,
                    )
                )
                pid += 1
    return products


def build_stores(cfg: GeneratorConfig, rng: random.Random) -> list[Store]:
    stores: list[Store] = []
    sid = 1
    for region in REGIONS:
        for n in range(cfg.stores_per_region):
            # Cycle channels across all stores so every channel in the controlled
            # vocabulary is represented in dim_channel.
            channel = CHANNELS[(sid - 1) % len(CHANNELS)]
            year = rng.randint(2016, 2023)
            month = rng.randint(1, 12)
            name = f"{region} {channel.title()} {n + 1}"
            stores.append(
                Store(
                    store_id=sid,
                    store_name=name,
                    channel=channel,
                    region=region,
                    opened_date=f"{year:04d}-{month:02d}-01",
                )
            )
            sid += 1
    return stores
