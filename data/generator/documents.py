"""Unstructured document sources: support tickets, reviews, quarterly reports.

These are the free-text side of the dataset (``docs/02-data-model.md`` §2.2). The
planted story is echoed here on purpose: 2026 Q2 carries a spike of negative
North-region Electronics reviews and fulfilment-backlog support tickets, and the
2026 Q2 business report names the cause — so "why did sales decline?" is
answerable by decomposition **and** cross-referenced against document themes,
and "summarize customer complaints" surfaces the same issue.

Bodies deliberately contain synthetic contact details (emails / phone numbers)
so the ingestion redaction pass has PII to mask before anything is indexed.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import asdict, dataclass

from .config import CATEGORIES, GeneratorConfig
from .entities import Customer, Product

# --- themed text fragments -----------------------------------------------------
_DIP_TICKET_SUBJECTS = [
    "Late delivery - North region electronics",
    "Where is my order? North warehouse delay",
    "Electronics shipment stuck at fulfilment centre",
    "Cancelled order due to weeks-long delay",
]
_DIP_TICKET_BODIES = [
    "Customer in the North region reports their electronics order arrived two "
    "weeks late due to a fulfilment centre backlog. Reachable at {email} or {phone}.",
    "Third complaint this week about North warehouse delays on electronics. "
    "Order was promised in three days and has not shipped. Contact: {email}.",
    "Operations flagged a persistent backlog at the North fulfilment centre; "
    "electronics shipments delayed, driving cancellations and refunds. Ref {phone}.",
]
_GENERIC_TICKET_SUBJECTS = [
    "Question about my order",
    "Return request",
    "Product setup help",
    "Billing question",
]
_GENERIC_TICKET_BODIES = [
    "Customer asked how to set up their new {category} purchase. Resolved on call. "
    "Callback number {phone}.",
    "Routine return request for a {category} item, within policy. Email {email}.",
    "General enquiry about delivery windows for {category}. No issues reported.",
]

_DIP_REVIEW_TITLES = [
    "Arrived two weeks late",
    "Never showed up on time",
    "Frustrated with the delivery delays",
    "Cancelled - shipping took forever",
]
_DIP_REVIEW_BODIES = [
    "Ordered from the North distribution centre and it took forever to ship. "
    "Cancelled a second item. Very frustrated with the electronics delivery delays.",
    "Two weeks late and no updates. The North warehouse clearly has a backlog. "
    "Would not order electronics from here again this quarter.",
]
_POS_REVIEW_TITLES = [
    "Great product",
    "Fast shipping, happy customer",
    "Exactly as described",
    "Would buy again",
]
_POS_REVIEW_BODIES = [
    "Love the new {category} range. Arrived quickly and works perfectly.",
    "No complaints at all - smooth delivery and good quality {category} item.",
    "Solid value for the price. Shipping was quick in my region.",
]


@dataclass(frozen=True)
class Document:
    doc_id: str
    doc_type: str  # ticket | review | report
    title: str
    body: str
    created_ts: str
    region: str | None
    category: str | None
    product_id: int | None
    # The SKU as well as the surrogate id: retrieval indexes this as
    # `product_ref`, and an exact SKU is what a lexical/sparse match keys on.
    product_sku: str | None
    customer_id: int | None
    rating: int | None
    author_role: str
    resolution_status: str | None
    period: str | None


def _rand_date_in(start: dt.date, end: dt.date, rng: random.Random) -> dt.date:
    span = (end - start).days
    return start + dt.timedelta(days=rng.randint(0, max(0, span)))


def _dip_window(cfg: GeneratorConfig) -> tuple[dt.date, dt.date]:
    q = cfg.dip_quarter
    start = dt.date(cfg.dip_year, (q - 1) * 3 + 1, 1)
    end_month = q * 3
    if end_month == 12:
        end = dt.date(cfg.dip_year, 12, 31)
    else:
        end = dt.date(cfg.dip_year, end_month + 1, 1) - dt.timedelta(days=1)
    return start, end


def build_documents(
    cfg: GeneratorConfig,
    rng: random.Random,
    customers: list[Customer],
    products: list[Product],
    n_tickets: int = 240,
    n_reviews: int = 400,
) -> list[Document]:
    docs: list[Document] = []
    by_cat = {c: [p for p in products if p.category == c] for c in CATEGORIES}
    north_customers = [c for c in customers if c.region == cfg.dip_region]
    all_customers = customers
    dip_start, dip_end = _dip_window(cfg)
    dip_products = by_cat[cfg.dip_category]

    # --- support tickets ------------------------------------------------------
    for n in range(n_tickets):
        is_dip = rng.random() < 0.35  # over-represent the backlog theme
        cust = rng.choice(north_customers if (is_dip and north_customers) else all_customers)
        if is_dip:
            product = rng.choice(dip_products)
            created = _rand_date_in(dip_start, dip_end, rng)
            subject = rng.choice(_DIP_TICKET_SUBJECTS)
            body = rng.choice(_DIP_TICKET_BODIES).format(email=cust.email, phone=cust.phone)
            region, category = cfg.dip_region, cfg.dip_category
            resolution = rng.choice(["open", "escalated", "resolved"])
        else:
            category = rng.choice(CATEGORIES)
            product = rng.choice(by_cat[category])
            created = _rand_date_in(cfg.start_date, cfg.end_date, rng)
            subject = rng.choice(_GENERIC_TICKET_SUBJECTS)
            body = rng.choice(_GENERIC_TICKET_BODIES).format(
                category=category.lower(), email=cust.email, phone=cust.phone
            )
            region = cust.region
            resolution = rng.choice(["resolved", "closed"])
        # Name the SKU in the text as well as the metadata: an exact identifier
        # is what the sparse/lexical half of hybrid retrieval matches on.
        body = f"{body} Item {product.sku}."
        docs.append(
            Document(
                doc_id=f"TICKET-{40000 + n}",
                doc_type="ticket",
                title=subject,
                body=body,
                created_ts=created.isoformat() + "T09:00:00",
                region=region,
                category=category,
                product_id=product.product_id,
                product_sku=product.sku,
                customer_id=cust.customer_id,
                rating=None,
                author_role="support_agent",
                resolution_status=resolution,
                period=None,
            )
        )

    # --- reviews --------------------------------------------------------------
    for n in range(n_reviews):
        is_dip = rng.random() < 0.30
        if is_dip and dip_products:
            product = rng.choice(dip_products)
            cust = rng.choice(north_customers if north_customers else all_customers)
            created = _rand_date_in(dip_start, dip_end, rng)
            rating = rng.choice([1, 1, 2, 2, 3])
            title = rng.choice(_DIP_REVIEW_TITLES)
            body = rng.choice(_DIP_REVIEW_BODIES)
            region, category = cfg.dip_region, cfg.dip_category
        else:
            category = rng.choice(CATEGORIES)
            product = rng.choice(by_cat[category])
            cust = rng.choice(all_customers)
            created = _rand_date_in(cfg.start_date, cfg.end_date, rng)
            rating = rng.choice([3, 4, 4, 5, 5, 5])
            title = rng.choice(_POS_REVIEW_TITLES)
            body = rng.choice(_POS_REVIEW_BODIES).format(category=category.lower())
            region = cust.region
        body = f"{body} Item {product.sku}."
        docs.append(
            Document(
                doc_id=f"REVIEW-{90000 + n}",
                doc_type="review",
                title=title,
                body=body,
                created_ts=created.isoformat() + "T12:00:00",
                region=region,
                category=category,
                product_id=product.product_id,
                product_sku=product.sku,
                customer_id=cust.customer_id,
                rating=rating,
                author_role="customer",
                resolution_status=None,
                period=None,
            )
        )

    # --- quarterly business reports ------------------------------------------
    docs.extend(_build_reports(cfg))
    # Stable ordering independent of generation order (deterministic output).
    docs.sort(key=lambda d: (d.doc_type, d.doc_id))
    return docs


def _build_reports(cfg: GeneratorConfig) -> list[Document]:
    reports: list[Document] = []
    # One report per quarter fully inside the date span.
    year = cfg.start_date.year
    quarter = (cfg.start_date.month - 1) // 3 + 1
    idx = 0
    while True:
        q_start = dt.date(year, (quarter - 1) * 3 + 1, 1)
        end_month = quarter * 3
        q_end = (
            dt.date(year, 12, 31)
            if end_month == 12
            else dt.date(year, end_month + 1, 1) - dt.timedelta(days=1)
        )
        if q_start < cfg.start_date or q_end > cfg.end_date:
            if q_start > cfg.end_date:
                break
            year, quarter = _next_quarter(year, quarter)
            continue
        period = f"{year}Q{quarter}"
        is_dip = year == cfg.dip_year and quarter == cfg.dip_quarter
        if is_dip:
            title = f"{period} operations review"
            body = (
                f"The {cfg.dip_region} fulfilment centre backlog was the dominant "
                f"operational issue of {period}, concentrated in {cfg.dip_category}. "
                "On-hand stock ran near zero and lead times stretched past six "
                "weeks, so shipments slipped and cancellations rose. Revenue for "
                f"{cfg.dip_region} {cfg.dip_category} fell sharply versus the prior "
                "quarter. Other regions operated normally. Remediation: temporary "
                "capacity and rerouting to neighbouring centres."
            )
        else:
            title = f"{period} performance summary"
            body = (
                f"{period} traded in line with expectations across regions and "
                "categories. Seasonal demand followed the usual pattern with no "
                "material operational incidents. Inventory levels stayed healthy."
            )
        reports.append(
            Document(
                doc_id=f"REPORT-{period}",
                doc_type="report",
                title=title,
                body=body,
                created_ts=q_end.isoformat() + "T17:00:00",
                region=cfg.dip_region if is_dip else None,
                category=cfg.dip_category if is_dip else None,
                product_id=None,
                product_sku=None,
                customer_id=None,
                rating=None,
                author_role="ops_manager",
                resolution_status=None,
                period=period,
            )
        )
        idx += 1
        year, quarter = _next_quarter(year, quarter)
        if year > cfg.end_date.year + 1:
            break
    return reports


def _next_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year + 1, 1) if quarter == 4 else (year, quarter + 1)


def document_to_dict(doc: Document) -> dict:
    return asdict(doc)
