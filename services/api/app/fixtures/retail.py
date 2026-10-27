"""Deterministic retail e-commerce fixture: star schema + sample documents.

Spans 2+ years of daily e-commerce transactions from January 2024 to
September 2026 for a mid-size Indian direct-to-consumer electronics and
lifestyle retailer ("ShopSphere").

Key design decisions
--------------------
* **40 real products** across 5 categories (Electronics, Apparel, Home &
  Kitchen, Beauty & Personal Care, Sports & Outdoors) with 15 subcategories.
* **40 customers** across 4 regions × 4 segments (Consumer / Business /
  Enterprise / Premium).
* **5 sales channels**: Website, Mobile App, Marketplace, Retail Store,
  Social Commerce.
* **Daily-level transactions** (~14,400 line items over 2.5 years) with
  realistic seasonal patterns: Q4 holiday surge, monsoon-season uptick for
  Home, summer dip in Apparel, promotional spikes on key sale dates.
* **Planted anomaly**: In Q2 2026 the North region's Electronics revenue
  drops ~55 % due to a fulfilment-centre backlog — this is the canonical
  "Why did sales decline?" story verified by golden tests.
* All golden-test expectations (text2sql.py) are maintained.
"""

from __future__ import annotations

import datetime as dt
import math

# ═══════════════════════════════════════════════════════════════════════════════
# DIMENSION DATA
# ═══════════════════════════════════════════════════════════════════════════════

REGIONS = ["North", "South", "East", "West"]

CATEGORIES = ["Electronics", "Apparel", "Home", "Beauty", "Sports"]

# 40 Real products: (id, name, category, subcategory, base_price)
PRODUCTS = [
    # ── Electronics (8) ───────────────────────────────────────────────────────
    (1,  "MacBook Pro 16\" M3 Max",              "Electronics", "Laptops",      249_999),
    (2,  "iPhone 15 Pro Max 256GB",              "Electronics", "Phones",       159_900),
    (3,  "Sony WH-1000XM5 Wireless Headphones",  "Electronics", "Audio",         29_990),
    (4,  "Samsung Galaxy S24 Ultra",             "Electronics", "Phones",       134_999),
    (5,  "Apple AirPods Pro 2nd Gen",            "Electronics", "Audio",         24_900),
    (6,  "Dell XPS 15 OLED Touch Laptop",        "Electronics", "Laptops",      179_990),
    (7,  "Sony PlayStation 5 Slim",              "Electronics", "Gaming",        49_990),
    (8,  "Apple iPad Air M2 256GB",              "Electronics", "Tablets",       74_900),
    # ── Apparel (8) ───────────────────────────────────────────────────────────
    (9,  "Nike Air Zoom Pegasus 40",             "Apparel",     "Footwear",      11_495),
    (10, "Levi's 511 Slim Fit Stretch Jeans",    "Apparel",     "Bottoms",        4_599),
    (11, "The North Face Resolve 2 Shell",       "Apparel",     "Outerwear",     10_999),
    (12, "Adidas Ultraboost Light Sneakers",     "Apparel",     "Footwear",      16_999),
    (13, "Patagonia Down Sweater Hoody",         "Apparel",     "Outerwear",     22_999),
    (14, "Lululemon Metal Vent Tech Tee",        "Apparel",     "Tops",           5_800),
    (15, "Vuori Sunday Performance Joggers",     "Apparel",     "Activewear",     7_499),
    (16, "Ray-Ban Aviator Classic Sunglasses",   "Apparel",     "Accessories",    8_990),
    # ── Home & Kitchen (8) ────────────────────────────────────────────────────
    (17, "Breville Barista Touch Espresso",      "Home",        "Kitchen",       54_990),
    (18, "Dyson V15 Detect Cordless Vacuum",     "Home",        "Appliances",    62_900),
    (19, "Herman Miller Aeron Office Chair",     "Home",        "Furniture",    129_900),
    (20, "Le Creuset 5.5 Qt Dutch Oven",         "Home",        "Kitchen",       29_990),
    (21, "Branch Adjustable Standing Desk",      "Home",        "Furniture",     42_990),
    (22, "Brooklinen Luxe Sateen Sheet Set",     "Home",        "Decor",          9_499),
    (23, "Philips Hue Starter Kit 4-Bulb",       "Home",        "Smart Home",    12_999),
    (24, "iRobot Roomba j7+ Self-Emptying",      "Home",        "Appliances",    49_990),
    # ── Beauty & Personal Care (8) ────────────────────────────────────────────
    (25, "Dyson Airwrap Multi-Styler",           "Beauty",      "Hair Care",     45_900),
    (26, "SK-II Facial Treatment Essence 230ml", "Beauty",      "Skincare",      13_500),
    (27, "Olaplex No.3 Hair Perfector",          "Beauty",      "Hair Care",      2_800),
    (28, "Charlotte Tilbury Pillow Talk Set",    "Beauty",      "Makeup",         5_900),
    (29, "La Mer Moisturizing Cream 60ml",       "Beauty",      "Skincare",      25_500),
    (30, "Foreo Luna 4 Facial Cleanser",         "Beauty",      "Devices",       17_999),
    (31, "Estee Lauder Night Repair Serum",      "Beauty",      "Skincare",       8_200),
    (32, "MAC Cosmetics Ruby Woo Lipstick",      "Beauty",      "Makeup",         1_950),
    # ── Sports & Outdoors (8) ─────────────────────────────────────────────────
    (33, "Garmin Fenix 7X Solar GPS Watch",      "Sports",      "Wearables",     74_990),
    (34, "Yeti Rambler 36oz Bottle",             "Sports",      "Hydration",      4_500),
    (35, "Osprey Atmos AG 65L Backpack",         "Sports",      "Bags",          24_990),
    (36, "Manduka PRO Yoga Mat 6mm",             "Sports",      "Fitness",        9_990),
    (37, "Hydro Flask 32oz Wide Mouth",          "Sports",      "Hydration",      3_490),
    (38, "Black Diamond Spot 400 Headlamp",      "Sports",      "Camping",        4_990),
    (39, "Theragun Prime Percussive Massager",   "Sports",      "Recovery",      19_990),
    (40, "Coleman Sundome 4-Person Tent",         "Sports",      "Camping",        8_990),
]

# 40 customers: (id, region, segment)
CUSTOMERS = [
    # North (10)
    (1,  "North", "Consumer"),  (2,  "North", "Consumer"),
    (3,  "North", "Business"),  (4,  "North", "Business"),
    (5,  "North", "Enterprise"),(6,  "North", "Premium"),
    (7,  "North", "Consumer"),  (8,  "North", "Premium"),
    (9,  "North", "Business"),  (10, "North", "Enterprise"),
    # South (10)
    (11, "South", "Consumer"),  (12, "South", "Consumer"),
    (13, "South", "Business"),  (14, "South", "Business"),
    (15, "South", "Enterprise"),(16, "South", "Premium"),
    (17, "South", "Consumer"),  (18, "South", "Premium"),
    (19, "South", "Business"),  (20, "South", "Enterprise"),
    # East (10)
    (21, "East",  "Consumer"),  (22, "East",  "Consumer"),
    (23, "East",  "Business"),  (24, "East",  "Business"),
    (25, "East",  "Enterprise"),(26, "East",  "Premium"),
    (27, "East",  "Consumer"),  (28, "East",  "Premium"),
    (29, "East",  "Business"),  (30, "East",  "Enterprise"),
    # West (10)
    (31, "West",  "Consumer"),  (32, "West",  "Consumer"),
    (33, "West",  "Business"),  (34, "West",  "Business"),
    (35, "West",  "Enterprise"),(36, "West",  "Premium"),
    (37, "West",  "Consumer"),  (38, "West",  "Premium"),
    (39, "West",  "Business"),  (40, "West",  "Enterprise"),
]

STORES = [
    (1, "Delhi Flagship Store",      "North"),
    (2, "Chandigarh Retail Outlet",  "North"),
    (3, "North Fulfilment Centre",   "North"),
    (4, "Bangalore MG Road Store",   "South"),
    (5, "Chennai Express Outlet",    "South"),
    (6, "South Distribution Hub",    "South"),
    (7, "Kolkata Park Street Store",  "East"),
    (8, "East Metro Fulfilment",     "East"),
    (9, "Mumbai Linking Road Store",  "West"),
    (10, "Pune Koregaon Park Store", "West"),
    (11, "West Coast Distribution",  "West"),
]

CHANNELS = [
    (1, "Website"),
    (2, "Mobile App"),
    (3, "Marketplace"),
    (4, "Retail Store"),
    (5, "Social Commerce"),
]

# ═══════════════════════════════════════════════════════════════════════════════
# REVENUE MODEL — daily revenue targets by region × category × date
# ═══════════════════════════════════════════════════════════════════════════════

# Annual baseline daily revenue (₹) per region × category — organic 2025 level
_DAILY_BASE: dict[tuple[str, str], int] = {
    # Electronics is the big-ticket driver
    ("North", "Electronics"): 22_000,  ("South", "Electronics"): 18_000,
    ("East",  "Electronics"): 14_000,  ("West",  "Electronics"): 16_000,
    # Apparel — broad, moderate
    ("North", "Apparel"):     13_000,  ("South", "Apparel"):     11_000,
    ("East",  "Apparel"):     10_000,  ("West",  "Apparel"):     10_500,
    # Home & Kitchen
    ("North", "Home"):         8_800,  ("South", "Home"):          7_700,
    ("East",  "Home"):         6_600,  ("West",  "Home"):          7_200,
    # Beauty & Personal Care
    ("North", "Beauty"):       5_500,  ("South", "Beauty"):        6_200,
    ("East",  "Beauty"):       4_800,  ("West",  "Beauty"):        5_000,
    # Sports & Outdoors
    ("North", "Sports"):       4_200,  ("South", "Sports"):        3_800,
    ("East",  "Sports"):       3_400,  ("West",  "Sports"):        3_600,
}


def _seasonal_mult(d: dt.date) -> float:
    """Seasonal multiplier for a given date (day-of-year curve).

    Peak in Oct-Nov (Diwali / festive season + Black Friday),
    secondary peak in Jan (New Year sales), trough in Feb-Mar.
    """
    doy = d.timetuple().tm_yday
    # Base sinusoidal: peak around day 305 (Nov 1), trough around day 120 (Apr 30)
    angle = 2 * math.pi * (doy - 305) / 365
    seasonal = 1.0 + 0.25 * math.cos(angle)

    # Promotional spikes
    m, day = d.month, d.day
    if m == 10 and 10 <= day <= 18:  # Diwali sale week
        seasonal *= 1.6
    elif m == 11 and 20 <= day <= 30:  # Black Friday + Cyber Monday
        seasonal *= 1.45
    elif m == 1 and 1 <= day <= 7:  # New Year sale
        seasonal *= 1.3
    elif m == 7 and 15 <= day <= 22:  # Prime Day equivalent
        seasonal *= 1.2
    elif m == 3 and 1 <= day <= 8:  # Women's Day sale
        seasonal *= 1.1

    return seasonal


def _yoy_growth(year: int) -> float:
    """Year-over-year growth multiplier (startup-style growth curve)."""
    return {2024: 0.82, 2025: 1.00, 2026: 1.14}.get(year, 1.0)


def _weekend_mult(d: dt.date) -> float:
    """Weekend boost — online shopping peaks on weekends."""
    return 1.15 if d.weekday() >= 5 else 1.0


def _category_seasonal(cat: str, d: dt.date) -> float:
    """Category-specific seasonal adjustments."""
    m = d.month
    if cat == "Apparel":
        # Winter outerwear surge Oct-Jan, summer dip Apr-Jun
        if m in (10, 11, 12, 1):
            return 1.25
        if m in (4, 5, 6):
            return 0.80
    elif cat == "Home":
        # Monsoon (Jul-Sep) drives appliance and furnishing purchases
        if m in (7, 8, 9):
            return 1.20
    elif cat == "Beauty":
        # Wedding season (Oct-Feb) and gifting drive beauty sales
        if m in (10, 11, 12, 1, 2):
            return 1.30
        if m in (6, 7):
            return 0.85
    elif cat == "Sports":
        # Fitness peak in Jan (resolutions) and Oct (marathon season)
        if m in (1, 10):
            return 1.35
        if m in (4, 5):
            return 0.90
    elif cat == "Electronics":
        # Back-to-school (Jun-Jul) mild bump
        if m in (6, 7):
            return 1.10
    return 1.0


def _planted_dip(region: str, cat: str, d: dt.date) -> float:
    """The canonical North Electronics Q2 2026 fulfilment collapse.

    This is the analytical story verified by golden tests: North+Electronics
    revenue drops ~55% in Q2 2026 due to a distribution-centre backlog.
    """
    if region == "North" and cat == "Electronics" and d.year == 2026 and 4 <= d.month <= 6:
        return 0.45  # severe drop
    if region == "North" and d.year == 2026 and 4 <= d.month <= 6:
        return 0.90  # mild drag on other North categories
    return 1.0


def _daily_revenue(region: str, cat: str, d: dt.date) -> int:
    """Compute the target daily net revenue for a region × category × date."""
    base = _DAILY_BASE[(region, cat)]
    mult = (
        _yoy_growth(d.year)
        * _seasonal_mult(d)
        * _weekend_mult(d)
        * _category_seasonal(cat, d)
        * _planted_dip(region, cat, d)
    )
    return max(500, int(base * mult))


def _mix(a: int, b: int) -> int:
    """Fast integer mixing — no strings, no allocations."""
    h = (a * 2654435761 + b) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x45d9f3b) & 0xFFFFFFFF
    h ^= h >> 16
    return h


# ═══════════════════════════════════════════════════════════════════════════════
# WAREHOUSE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

# Map region/cat names to stable integer ids for fast hashing
_REGION_ID = {r: i for i, r in enumerate(REGIONS)}
_CAT_ID = {c: i for i, c in enumerate(CATEGORIES)}


def _split_revenue(total: int, n: int, seed: int) -> list[int]:
    """Split total into n unequal positive ints summing exactly to total."""
    if n <= 0:
        return []
    raw = [1 + _mix(seed, i) % 100 for i in range(n)]
    s = sum(raw)
    parts = [max(1, int(total * r / s)) for r in raw]
    diff = total - sum(parts)
    parts[-1] += diff
    return parts


def build_retail_warehouse(con) -> None:
    """Create and populate the star schema on an open DuckDB connection."""
    _create_schema(con)

    # --- dim_date: daily rows from 2024-01-01 to 2026-06-30 ------------------
    cur = dt.date(2024, 1, 1)
    end = dt.date(2026, 6, 30)
    dim_date: list[tuple] = []
    while cur <= end:
        dk = int(cur.strftime("%Y%m%d"))
        fd = cur.strftime("%Y-%m-%d")
        wk = f"{cur.year}-W{cur.isocalendar()[1]:02d}"
        mo = cur.strftime("%Y-%m")
        q = f"{cur.year}Q{(cur.month - 1) // 3 + 1}"
        dim_date.append((dk, fd, wk, mo, q, cur.year))
        cur += dt.timedelta(days=1)
    date_vals = ",".join(f"({r[0]},'{r[1]}','{r[2]}','{r[3]}','{r[4]}',{r[5]})" for r in dim_date)
    con.execute(f"INSERT INTO dim_date VALUES {date_vals}")

    # --- dimensions -----------------------------------------------------------
    con.executemany("INSERT INTO dim_product VALUES (?,?,?,?)",
                    [(p[0], p[1], p[2], p[3]) for p in PRODUCTS])
    con.executemany("INSERT INTO dim_customer VALUES (?,?,?)", CUSTOMERS)
    con.executemany("INSERT INTO dim_store VALUES (?,?,?)", STORES)
    con.executemany("INSERT INTO dim_channel VALUES (?,?)", CHANNELS)

    # Pre-compute lookup indices
    products_by_cat: dict[str, list[tuple]] = {}
    for p in PRODUCTS:
        products_by_cat.setdefault(p[2], []).append(p)

    customers_by_region: dict[str, list[int]] = {}
    for c in CUSTOMERS:
        customers_by_region.setdefault(c[1], []).append(c[0])

    stores_by_region: dict[str, list[int]] = {}
    for s in STORES:
        stores_by_region.setdefault(s[2], []).append(s[0])

    # --- fact_order_items: daily transactions ---------------------------------
    order_items: list[tuple] = []
    oi_key = 1
    order_key = 1

    cur_date = dt.date(2024, 1, 1)
    end_date = dt.date(2026, 6, 30)
    while cur_date <= end_date:
        date_k = int(cur_date.strftime("%Y%m%d"))
        day_ord = cur_date.toordinal()

        for ri, region in enumerate(REGIONS):
            for ci, cat in enumerate(CATEGORIES):
                daily_target = _daily_revenue(region, cat, cur_date)
                day_seed = _mix(day_ord, ri * 7 + ci)

                # Number of line items per day depends on category
                if cat == "Electronics":
                    n_items = 2 + day_seed % 3
                elif cat in ("Beauty", "Sports"):
                    n_items = 3 + day_seed % 4
                else:
                    n_items = 2 + day_seed % 4

                splits = _split_revenue(daily_target, n_items, day_seed)
                cat_products = products_by_cat[cat]
                reg_customers = customers_by_region[region]
                reg_stores = stores_by_region[region]

                for i, net_rev in enumerate(splits):
                    seed = _mix(day_seed, i + 1000)
                    pid = cat_products[seed % len(cat_products)][0]
                    cid = reg_customers[(seed >> 4) % len(reg_customers)]
                    sid = reg_stores[(seed >> 8) % len(reg_stores)]
                    chid = ((seed >> 12) % 5) + 1

                    base_price = cat_products[seed % len(cat_products)][4]
                    qty = max(1, net_rev // base_price) if base_price > 0 else 1
                    gross = int(net_rev / 0.88)  # ~12% avg discount
                    discount = gross - net_rev
                    cost = int(gross * 0.58)  # ~42% gross margin

                    # Returns: ~6% of items, higher for Apparel
                    return_threshold = 8 if cat == "Apparel" else 6
                    is_returned = (seed % 100) < return_threshold

                    order_items.append((
                        oi_key, order_key, date_k,
                        pid, cid, sid, chid,
                        qty, gross, discount, cost, is_returned,
                    ))
                    oi_key += 1
                    order_key += 1

        cur_date += dt.timedelta(days=1)

    batch_size = 5000
    for i in range(0, len(order_items), batch_size):
        batch = order_items[i : i + batch_size]
        vals = ",".join(
            "({},{})".format(
                ",".join(str(v) for v in r[:11]),
                "true" if r[11] else "false",
            )
            for r in batch
        )
        con.execute(f"INSERT INTO fact_order_items VALUES {vals}")

    # --- fact_inventory_snapshot (current stock levels) ------------------------
    inv: list[tuple] = []
    snapshot_date = 20260515
    for p in PRODUCTS:
        pid, _, cat, _, _ = p
        for ri, region in enumerate(REGIONS):
            sid = stores_by_region[region][0]
            seed = _mix(pid * 31, ri * 17 + 999)
            # North Electronics: artificially low from the backlog
            if cat == "Electronics" and region == "North":
                on_hand = 15 + seed % 30
            elif cat == "Electronics":
                on_hand = 80 + seed % 120
            elif cat in ("Beauty", "Sports"):
                on_hand = 150 + seed % 200
            else:
                on_hand = 100 + seed % 150
            reorder = max(20, on_hand // 3)
            reserved = seed % max(1, on_hand // 5)
            lead_days = 7 + seed % 21
            inv.append((pid, sid, snapshot_date, on_hand, reserved, reorder, lead_days))
    con.executemany(
        "INSERT INTO fact_inventory_snapshot VALUES (?,?,?,?,?,?,?)", inv,
    )


def _create_schema(con) -> None:
    con.execute("""
        CREATE TABLE dim_date (
            date_key INTEGER, full_date DATE, week_label VARCHAR,
            month_label VARCHAR, quarter_label VARCHAR, cal_year INTEGER
        );
        CREATE TABLE dim_product (
            product_key INTEGER, product_name VARCHAR,
            category VARCHAR, subcategory VARCHAR
        );
        CREATE TABLE dim_customer (
            customer_key INTEGER, region VARCHAR, segment VARCHAR
        );
        CREATE TABLE dim_store (
            store_key INTEGER, store_name VARCHAR, region VARCHAR
        );
        CREATE TABLE dim_channel (
            channel_key INTEGER, channel_name VARCHAR
        );
        CREATE TABLE fact_order_items (
            order_item_key INTEGER, order_key INTEGER, date_key INTEGER,
            product_key INTEGER, customer_key INTEGER, store_key INTEGER,
            channel_key INTEGER, quantity INTEGER, gross_revenue BIGINT,
            discount_amount BIGINT, cost_amount BIGINT, is_returned BOOLEAN
        );
        CREATE TABLE fact_inventory_snapshot (
            product_key INTEGER, store_key INTEGER, date_key INTEGER,
            units_on_hand INTEGER, units_reserved INTEGER,
            reorder_point INTEGER, lead_time_days INTEGER
        );
    """)


# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE DOCUMENTS — 35 rich e-commerce documents
# ═══════════════════════════════════════════════════════════════════════════════

def get_sample_documents() -> list[dict]:
    """Rich corpus of customer tickets, reviews, operations reports, and marketing briefs."""
    return [
        # ── Canonical Q2 2026 North Electronics dip documents ─────────────────
        {
            "doc_id": "TICKET-40122", "source_type": "ticket",
            "title": "Late delivery — North region electronics",
            "body": (
                "Customer in Delhi reports their MacBook Pro 16\" order arrived "
                "two weeks late due to a fulfilment centre backlog at the North "
                "distribution hub. This is the third complaint this week about "
                "North warehouse delays affecting electronics shipments."
            ),
            "date": "2026-05-08", "region": "North", "category": "Electronics",
            "author_role": "agent",
        },
        {
            "doc_id": "TICKET-40189", "source_type": "ticket",
            "title": "PS5 Slim order cancelled — North stock-out",
            "body": (
                "Customer escalated after Sony PS5 Slim showed 'In Stock' but "
                "was auto-cancelled 48 hours later. North fulfilment centre "
                "confirmed inventory discrepancy — system shows 200 units but "
                "physical count is 12. Issued full refund + ₹500 credit."
            ),
            "date": "2026-05-14", "region": "North", "category": "Electronics",
            "author_role": "agent",
        },
        {
            "doc_id": "REVIEW-9931", "source_type": "review",
            "title": "Arrived two weeks late — very frustrated",
            "body": (
                "Ordered a Dell XPS 15 laptop, it took forever to ship from "
                "the North distribution centre. Cancelled a second item "
                "(AirPods Pro). Very frustrated with the delivery delays on "
                "electronics. Moving my next purchase to Amazon."
            ),
            "date": "2026-05-19", "region": "North", "category": "Electronics",
            "author_role": "customer",
        },
        {
            "doc_id": "TICKET-40210", "source_type": "ticket",
            "title": "North fulfilment backlog escalation — Ops",
            "body": (
                "Operations flagged a persistent backlog at the North "
                "fulfilment centre through April and May. Electronics "
                "shipments delayed 10-14 days on average, driving a 38% "
                "increase in cancellations and a 22% uptick in refund "
                "requests compared to Q1."
            ),
            "date": "2026-05-27", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REPORT-Q2-OPS", "source_type": "report",
            "title": "Q2 2026 Operations Review — North crisis debrief",
            "body": (
                "The North fulfilment centre backlog was the dominant "
                "operational issue of Q2. Root cause: a warehouse management "
                "system migration that went live April 3 introduced scanning "
                "errors, causing a 60% drop in pick-pack throughput for "
                "electronics (large items). Other regions operated normally. "
                "Remediation: temporary overflow capacity commissioned at a "
                "3PL partner and system rollback completed May 28."
            ),
            "date": "2026-06-30", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },

        # ── 2024 Historical Documents ─────────────────────────────────────────
        {
            "doc_id": "REVIEW-2024-0215", "source_type": "review",
            "title": "MacBook Pro M3 Max — phenomenal machine",
            "body": (
                "Received my MacBook Pro 16\" in West region in two days. "
                "Phenomenal build quality, battery life lasts all day. The M3 "
                "Max handles 4K video editing and large ML models without "
                "breaking a sweat. Best purchase of the year."
            ),
            "date": "2024-02-15", "region": "West", "category": "Electronics",
            "author_role": "customer",
        },
        {
            "doc_id": "TICKET-2024-0412", "source_type": "ticket",
            "title": "Bulk sneaker order — sizing exchange request",
            "body": (
                "Customer ordered 5 pairs of Nike Pegasus 40 and Adidas "
                "Ultraboost Light in mixed sizes. Requested exchange for 2 "
                "pairs — half size up. Exchange initiated under satisfaction "
                "guarantee; return pick-up scheduled."
            ),
            "date": "2024-04-12", "region": "South", "category": "Apparel",
            "author_role": "agent",
        },
        {
            "doc_id": "REPORT-2024-Q2", "source_type": "report",
            "title": "Q2 2024 — Mobile App channel overtakes Marketplace",
            "body": (
                "Mobile App transactions surpassed Marketplace for the first "
                "time in June. App-driven AOV is 18% higher than Marketplace "
                "due to personalised recommendations and exclusive app-only "
                "flash sales. Push notifications drove 34% of app sessions."
            ),
            "date": "2024-06-30", "region": "South", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REVIEW-2024-0803", "source_type": "review",
            "title": "Dyson Airwrap transformed my morning routine",
            "body": (
                "The Dyson Airwrap is worth every rupee. Multiple attachments "
                "for different hair types, dries and styles simultaneously. "
                "Delivery to Kolkata was quick — arrived in 2 days."
            ),
            "date": "2024-08-03", "region": "East", "category": "Beauty",
            "author_role": "customer",
        },
        {
            "doc_id": "REPORT-2024-Q4", "source_type": "report",
            "title": "2024 Diwali + Black Friday performance report",
            "body": (
                "Record festive season: total revenue crossed ₹42L across "
                "Oct 10-Nov 30. Electronics drove 44% of the total with "
                "MacBook Pro and iPhone 15 as hero SKUs. Beauty category "
                "surprised with 55% QoQ growth, led by SK-II and Charlotte "
                "Tilbury gift sets. Delivery SLA met at 98.6% across all "
                "four regions."
            ),
            "date": "2024-12-15", "region": "South", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REVIEW-2024-1125", "source_type": "review",
            "title": "Garmin Fenix 7X — ultimate trail watch",
            "body": (
                "Used the Garmin Fenix 7X on a 5-day Himalayan trek. Solar "
                "charging kept it alive the entire trip. GPS accuracy was "
                "spot-on even in deep valleys. Topo maps are excellent."
            ),
            "date": "2024-11-25", "region": "North", "category": "Sports",
            "author_role": "customer",
        },

        # ── 2025 Historical Documents ─────────────────────────────────────────
        {
            "doc_id": "REVIEW-2025-0218", "source_type": "review",
            "title": "Breville Barista Touch — cafe-quality at home",
            "body": (
                "The Breville Barista Touch transformed our kitchen into a "
                "boutique cafe. Touch screen guidance is intuitive and "
                "espresso extraction is rich. Paired it with the Fellow Ode "
                "grinder — absolute perfection."
            ),
            "date": "2025-02-18", "region": "East", "category": "Home",
            "author_role": "customer",
        },
        {
            "doc_id": "TICKET-2025-0310", "source_type": "ticket",
            "title": "Enterprise bulk order — Aeron chairs × 40",
            "body": (
                "Enterprise client (IT company) in Bangalore requested a "
                "quote for 40 Herman Miller Aeron chairs for new office "
                "floor. Business discount of 12% applied. Freight delivery "
                "scheduled over 2 weeks."
            ),
            "date": "2025-03-10", "region": "South", "category": "Home",
            "author_role": "agent",
        },
        {
            "doc_id": "REVIEW-2025-0414", "source_type": "review",
            "title": "Sony WH-1000XM5 — silence is golden",
            "body": (
                "Best wireless headphones on the market. ANC completely "
                "silences cabin noise on flights, and microphone quality on "
                "Zoom calls is crystal clear. 30-hour battery life means I "
                "charge once a week."
            ),
            "date": "2025-04-14", "region": "North", "category": "Electronics",
            "author_role": "customer",
        },
        {
            "doc_id": "REPORT-2025-Q2", "source_type": "report",
            "title": "Q2 2025 — Social Commerce channel launch results",
            "body": (
                "Social Commerce channel (Instagram + WhatsApp) launched in "
                "April. By June it contributed 8% of total revenue. Beauty "
                "and Apparel categories accounted for 72% of social orders. "
                "Customer acquisition cost via social is 35% lower than "
                "Marketplace."
            ),
            "date": "2025-06-30", "region": "West", "category": "Beauty",
            "author_role": "manager",
        },
        {
            "doc_id": "TICKET-2025-0722", "source_type": "ticket",
            "title": "Premium customer — standing desk bulk order",
            "body": (
                "Premium segment client in Pune ordered 25 Branch Adjustable "
                "Standing Desks for co-working space. Requested white-glove "
                "delivery and on-site assembly. Scheduled over 3 business days."
            ),
            "date": "2025-07-22", "region": "West", "category": "Home",
            "author_role": "agent",
        },
        {
            "doc_id": "REVIEW-2025-0901", "source_type": "review",
            "title": "Osprey Atmos AG 65L — perfect for Ladakh trip",
            "body": (
                "Took the Osprey Atmos AG 65L on a 10-day Ladakh motorcycle "
                "trip. Anti-gravity suspension made it comfortable even fully "
                "loaded. Rain cover handled unexpected downpours. Highly "
                "recommend for adventure travelers."
            ),
            "date": "2025-09-01", "region": "North", "category": "Sports",
            "author_role": "customer",
        },
        {
            "doc_id": "REPORT-2025-Q3", "source_type": "report",
            "title": "Q3 2025 — Monsoon season Home category surge",
            "body": (
                "Home category revenue grew 22% QoQ during the monsoon "
                "season (Jul-Sep). Key drivers: Dyson V15 vacuum (+45% "
                "units), Roomba j7+ (+38%), and Philips Hue smart home "
                "products. Theory: people spending more time indoors drove "
                "home improvement purchases."
            ),
            "date": "2025-09-30", "region": "East", "category": "Home",
            "author_role": "manager",
        },
        {
            "doc_id": "REPORT-2025-Q4", "source_type": "report",
            "title": "2025 Annual e-commerce growth debrief",
            "body": (
                "Total annual turnover crossed ₹1.8Cr with 28% YoY growth. "
                "Mobile App channel grew 40% to become primary driver for "
                "Apparel and Beauty. Premium segment grew 52% — highest "
                "across all segments. Sports & Outdoors emerged as fastest-"
                "growing category at +34% YoY."
            ),
            "date": "2025-12-28", "region": "West", "category": "Electronics",
            "author_role": "manager",
        },

        # ── 2026 Current Year Documents ───────────────────────────────────────
        {
            "doc_id": "REPORT-2026-Q1", "source_type": "report",
            "title": "Q1 2026 — Strong start with New Year sale momentum",
            "body": (
                "Q1 opened with a record New Year sale (Jan 1-7) that drove "
                "18% of the quarter's total revenue. Electronics led with "
                "Samsung Galaxy S24 Ultra as the top-selling SKU. Beauty "
                "category saw strong wedding-season demand for SK-II and "
                "La Mer gift sets."
            ),
            "date": "2026-03-31", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REVIEW-2026-0204", "source_type": "review",
            "title": "Samsung Galaxy S24 Ultra — incredible display",
            "body": (
                "Upgraded to Galaxy S24 Ultra in East region. The anti-"
                "reflective screen coating is game-changing in Indian "
                "sunlight, and the 100x Space Zoom captures stunning "
                "architectural detail at the Victoria Memorial."
            ),
            "date": "2026-02-04", "region": "East", "category": "Electronics",
            "author_role": "customer",
        },
        {
            "doc_id": "REVIEW-2026-0318", "source_type": "review",
            "title": "La Mer cream — worth the splurge for wedding prep",
            "body": (
                "Bought the La Mer Moisturizing Cream for pre-wedding skincare. "
                "My skin has never looked this hydrated and radiant. The 60ml "
                "jar lasts about 3 months with daily use. Premium packaging "
                "made it feel like a luxury experience."
            ),
            "date": "2026-03-18", "region": "South", "category": "Beauty",
            "author_role": "customer",
        },
        {
            "doc_id": "REVIEW-2026-0420", "source_type": "review",
            "title": "Apparel quality dropping — disappointed",
            "body": (
                "Ordered Levi's 511 jeans and Vuori joggers. Jeans stitching "
                "came apart after 2 washes — clearly a QC issue. Vuori "
                "joggers are fine but overpriced. Returned the jeans."
            ),
            "date": "2026-04-20", "region": "West", "category": "Apparel",
            "author_role": "customer",
        },
        {
            "doc_id": "TICKET-2026-0502", "source_type": "ticket",
            "title": "Marketplace price-match request — iPhone 15 Pro Max",
            "body": (
                "Customer found iPhone 15 Pro Max ₹4,000 cheaper on Amazon "
                "during Summer Sale. Requested price match. Approved under "
                "competitive pricing policy; issued store credit for the "
                "difference to retain the customer."
            ),
            "date": "2026-05-02", "region": "South", "category": "Electronics",
            "author_role": "agent",
        },
        {
            "doc_id": "REVIEW-2026-0615", "source_type": "review",
            "title": "Theragun Prime — recovery game changer",
            "body": (
                "Started using the Theragun Prime after marathon training. "
                "5 speed settings, surprisingly quiet at lower speeds. "
                "Battery lasts about 2 hours of continuous use. My post-"
                "run soreness reduced dramatically."
            ),
            "date": "2026-06-15", "region": "South", "category": "Sports",
            "author_role": "customer",
        },
        {
            "doc_id": "REVIEW-2026-0712", "source_type": "review",
            "title": "Herman Miller Aeron — investment in posture",
            "body": (
                "Worth every single rupee. Fully adjustable lumbar support "
                "and breathable mesh keep my posture correct through long "
                "12-hour coding sessions. The 12-year warranty shows the "
                "company stands behind quality."
            ),
            "date": "2026-07-12", "region": "North", "category": "Home",
            "author_role": "customer",
        },
        {
            "doc_id": "REPORT-2026-Q2", "source_type": "report",
            "title": "Q2 2026 — North logistics crisis impact analysis",
            "body": (
                "North region Electronics revenue declined 55% QoQ — the "
                "largest single-segment drop in company history. Cancellation "
                "rate in North Electronics hit 34% (vs 6% company average). "
                "Customer NPS for North dropped from 72 to 41. Other regions "
                "and categories were largely unaffected. Estimated revenue "
                "impact: ₹14-16L in lost sales."
            ),
            "date": "2026-06-30", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REPORT-2026-Q3-RECOVERY", "source_type": "report",
            "title": "Q3 2026 — North logistics recovery & SLA restoration",
            "body": (
                "Following the Q2 fulfilment disaster, temporary 3PL overflow "
                "capacity was commissioned in July. WMS system fully restored "
                "with new scanning firmware. Backlog cleared by July 22. "
                "On-time delivery recovered to 99.1% by August. Q3 North "
                "Electronics revenue tracking at 108% of Q1 baseline — "
                "indicating a full recovery with pent-up demand rebound."
            ),
            "date": "2026-08-30", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REVIEW-2026-0825", "source_type": "review",
            "title": "iPad Air M2 — perfect for college",
            "body": (
                "Bought the iPad Air M2 for college. Apple Pencil note-"
                "taking is incredible, Stage Manager lets me multitask "
                "properly, and it's lighter than carrying textbooks. "
                "Delivered next day to Chandigarh."
            ),
            "date": "2026-08-25", "region": "North", "category": "Electronics",
            "author_role": "customer",
        },
        {
            "doc_id": "REVIEW-2026-0905", "source_type": "review",
            "title": "Dyson V15 — laser dust detection is magic",
            "body": (
                "The laser dust detection on the Dyson V15 is a game changer "
                "for Indian homes. Shows exactly how much dust you're picking "
                "up. Light and easy to maneuver even on stairs. Delivered "
                "next day to Bangalore in perfect condition."
            ),
            "date": "2026-09-05", "region": "South", "category": "Home",
            "author_role": "customer",
        },
        {
            "doc_id": "TICKET-2026-0910", "source_type": "ticket",
            "title": "Social Commerce — influencer campaign tracking",
            "body": (
                "Marketing team reports that the Sep Instagram Reels campaign "
                "for Ray-Ban Aviators and Lululemon tops drove 2,400 click-"
                "throughs and 180 conversions in 5 days. ROAS of 4.2x. "
                "Requesting budget increase for Diwali campaign."
            ),
            "date": "2026-09-10", "region": "West", "category": "Apparel",
            "author_role": "manager",
        },
        {
            "doc_id": "REVIEW-2026-0912", "source_type": "review",
            "title": "Philips Hue — smart home starter kit review",
            "body": (
                "Set up the Philips Hue 4-bulb starter kit in my Mumbai "
                "apartment. The color ambiance transforms the living room. "
                "Works perfectly with Alexa and Google Home. Wish it came "
                "with more bulbs for the price."
            ),
            "date": "2026-09-12", "region": "West", "category": "Home",
            "author_role": "customer",
        },
        {
            "doc_id": "REVIEW-2026-0918", "source_type": "review",
            "title": "SK-II Essence — holy grail skincare",
            "body": (
                "Been using SK-II Facial Treatment Essence for 6 months. "
                "The Pitera complex genuinely improved my skin texture and "
                "reduced dark spots. Expensive but the 230ml bottle lasts "
                "about 4 months. Reordering from ShopSphere for the loyalty "
                "points."
            ),
            "date": "2026-09-18", "region": "East", "category": "Beauty",
            "author_role": "customer",
        },
        {
            "doc_id": "REVIEW-2024-0950", "source_type": "review",
            "title": "Great apparel selection — fast South shipping",
            "body": (
                "Love the new apparel range, fast shipping in the South "
                "region. Nike Pegasus and North Face jacket arrived in "
                "perfect condition. No complaints at all."
            ),
            "date": "2024-05-11", "region": "South", "category": "Apparel",
            "author_role": "customer",
        },
    ]
