"""Deterministic retail fixture: a small star schema + sample documents.

This lets the whole insight engine run end-to-end with no external database. The
data has a *planted* story so "why did sales decline last quarter?" has a real,
cross-referenceable cause: in 2026Q2, **Electronics in the North region** drops
sharply (fulfilment delays), and the sample tickets/reviews for that period say
exactly that. See ``docs/02-data-model.md`` §8.
"""

from __future__ import annotations

REGIONS = ["North", "South", "East", "West"]
CATEGORIES = ["Electronics", "Apparel", "Home"]

# Q1 2026 net revenue (currency units, thousands) per region x category.
_Q1_REVENUE = {
    ("North", "Electronics"): 200_000, ("North", "Apparel"): 120_000, ("North", "Home"): 80_000,
    ("South", "Electronics"): 150_000, ("South", "Apparel"): 100_000, ("South", "Home"): 70_000,
    ("East", "Electronics"): 130_000, ("East", "Apparel"): 90_000, ("East", "Home"): 60_000,
    ("West", "Electronics"): 140_000, ("West", "Apparel"): 95_000, ("West", "Home"): 65_000,
}


def _q2_revenue(region: str, category: str) -> int:
    """Q2 with the planted dip: North+Electronics collapses; rest softens mildly."""
    base = _Q1_REVENUE[(region, category)]
    if region == "North" and category == "Electronics":
        return int(base * 0.45)   # the planted decline (fulfilment delays)
    if region == "North":
        return int(base * 0.90)
    return int(base * 0.98)


def _split_int(total: int, n: int) -> list[int]:
    """Split ``total`` into ``n`` positive ints summing exactly to total."""
    base = total // n
    parts = [base] * n
    parts[-1] += total - base * n
    return parts


# Representative month date_key per quarter (dim_date rows below).
_QUARTER_MONTH = {"2026Q1": 2, "2026Q2": 5}


def build_retail_warehouse(con) -> None:
    """Create and populate the star schema on an open DuckDB connection."""
    _create_schema(con)

    # --- dimensions -----------------------------------------------------------
    dim_date = []
    for m in range(1, 7):
        q = "2026Q1" if m <= 3 else "2026Q2"
        dim_date.append(
            (m, f"2026-{m:02d}-01", f"2026-W{m*4:02d}", f"2026-{m:02d}", q, 2026)
        )
    con.executemany("INSERT INTO dim_date VALUES (?,?,?,?,?,?)", dim_date)

    products = []  # (product_key, name, category, subcategory)
    pk = 1
    product_by_cat: dict[str, list[int]] = {c: [] for c in CATEGORIES}
    for cat in CATEGORIES:
        for i in range(1, 3):
            products.append((pk, f"{cat} Item {i}", cat, f"{cat} Sub {i}"))
            product_by_cat[cat].append(pk)
            pk += 1
    con.executemany("INSERT INTO dim_product VALUES (?,?,?,?)", products)

    customers = []  # (customer_key, region, segment)
    ck = 1
    cust_by_region: dict[str, list[int]] = {r: [] for r in REGIONS}
    for r in REGIONS:
        for seg in ("Consumer", "Business"):
            customers.append((ck, r, seg))
            cust_by_region[r].append(ck)
            ck += 1
    con.executemany("INSERT INTO dim_customer VALUES (?,?,?)", customers)

    stores = [(i + 1, f"{r} Store", r) for i, r in enumerate(REGIONS)]
    store_by_region = {r: i + 1 for i, r in enumerate(REGIONS)}
    con.executemany("INSERT INTO dim_store VALUES (?,?,?)", stores)

    channels = [(1, "Online"), (2, "Retail")]
    con.executemany("INSERT INTO dim_channel VALUES (?,?)", channels)

    # --- facts ----------------------------------------------------------------
    order_items = []
    order_key = 1
    oi_key = 1
    orders_per_combo = 4
    for quarter, month in _QUARTER_MONTH.items():
        for r in REGIONS:
            for cat in CATEGORIES:
                target = _Q1_REVENUE[(r, cat)] if quarter == "2026Q1" else _q2_revenue(r, cat)
                for i, net in enumerate(_split_int(target, orders_per_combo)):
                    gross = round(net / 0.9)
                    discount = gross - net           # net = gross - discount exactly
                    cost = round(gross * 0.6)
                    is_returned = (i == 0)            # one returned line per combo
                    qty = max(1, net // 5000)
                    order_items.append((
                        oi_key, order_key, month,
                        product_by_cat[cat][i % 2],
                        cust_by_region[r][i % 2],
                        store_by_region[r],
                        1 if i % 2 == 0 else 2,       # channel
                        qty, gross, discount, cost, is_returned,
                    ))
                    oi_key += 1
                    order_key += 1

    con.executemany(
        "INSERT INTO fact_order_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", order_items
    )

    # --- inventory snapshot (for restock questions) ---------------------------
    inv = []
    for cat in CATEGORIES:
        for p in product_by_cat[cat]:
            for r in REGIONS:
                on_hand = 40 if cat == "Electronics" and r == "North" else 250
                inv.append((p, store_by_region[r], 5, on_hand, 10, 60, 21))
    con.executemany("INSERT INTO fact_inventory_snapshot VALUES (?,?,?,?,?,?,?)", inv)


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


# --- sample documents (unstructured side) -------------------------------------
def get_sample_documents() -> list[dict]:
    """A handful of tickets/reviews/reports. The Q2 North items explain the dip."""
    return [
        {
            "doc_id": "TICKET-40122", "source_type": "ticket",
            "title": "Late delivery — North region electronics",
            "body": "Customer in the North region reports their electronics order "
                    "arrived two weeks late due to a fulfilment centre backlog. "
                    "Third complaint this week about North warehouse delays.",
            "date": "2026-05-08", "region": "North", "category": "Electronics",
            "author_role": "agent",
        },
        {
            "doc_id": "REVIEW-9931", "source_type": "review",
            "title": "Arrived two weeks late",
            "body": "Ordered a laptop, it took forever to ship from the North "
                    "distribution centre. Cancelled a second item. Very frustrated "
                    "with the delivery delays on electronics.",
            "date": "2026-05-19", "region": "North", "category": "Electronics",
            "author_role": "customer",
        },
        {
            "doc_id": "TICKET-40210", "source_type": "ticket",
            "title": "North fulfilment backlog escalation",
            "body": "Operations flagged a persistent backlog at the North fulfilment "
                    "centre through April and May, delaying electronics shipments and "
                    "driving cancellations and refunds.",
            "date": "2026-05-27", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },
        {
            "doc_id": "REVIEW-9950", "source_type": "review",
            "title": "Great apparel selection",
            "body": "Love the new apparel range, fast shipping in the South. No "
                    "complaints at all.",
            "date": "2026-05-11", "region": "South", "category": "Apparel",
            "author_role": "customer",
        },
        {
            "doc_id": "REPORT-Q2-OPS", "source_type": "report",
            "title": "Q2 operations review",
            "body": "The North fulfilment centre backlog was the dominant operational "
                    "issue of the quarter, concentrated in electronics. Other regions "
                    "operated normally. Remediation: temporary capacity and rerouting.",
            "date": "2026-06-30", "region": "North", "category": "Electronics",
            "author_role": "manager",
        },
    ]
