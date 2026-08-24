"""A small, runnable sample document set.

Shape matches ``services/api/app/fixtures/retail.py::get_sample_documents`` so
the same JSON drives the fixture retriever and this real pipeline. The story is
planted to be cross-referenceable (docs/02 §8): in 2026Q2 the North-region
electronics fulfilment centre backlogs, and the tickets/reviews/report below say
exactly that — so "why did North electronics sales decline?" has real evidence,
while the South apparel review is a negative control that must NOT match it.

The report (``REPORT-Q2-OPS``) carries Markdown headings so the heading-aware
chunker has something to split on; the others are whole-record.
"""

from __future__ import annotations

SAMPLE_DOCUMENTS: list[dict] = [
    {
        "doc_id": "TICKET-40122",
        "source_type": "ticket",
        "title": "Late delivery — North region electronics",
        "body": (
            "Customer in the North region reports their electronics order (order "
            "ORD-88213, product X230) arrived two weeks late due to a fulfilment "
            "centre backlog. Third complaint this week about North warehouse delays."
        ),
        "date": "2026-05-08",
        "region": "North",
        "category": "Electronics",
        "product_ref": "X230",
        "order_ref": "ORD-88213",
        "author_role": "agent",
    },
    {
        "doc_id": "REVIEW-9931",
        "source_type": "review",
        "title": "Arrived two weeks late",
        "body": (
            "Ordered the X230 laptop, it took forever to ship from the North "
            "distribution centre. Cancelled a second item. Very frustrated with the "
            "delivery delays on electronics."
        ),
        "date": "2026-05-19",
        "region": "North",
        "category": "Electronics",
        "product_ref": "X230",
        "author_role": "customer",
    },
    {
        "doc_id": "TICKET-40210",
        "source_type": "ticket",
        "title": "North fulfilment backlog escalation",
        "body": (
            "Operations flagged a persistent backlog at the North fulfilment centre "
            "through April and May, delaying electronics shipments and driving "
            "cancellations and refunds."
        ),
        "date": "2026-05-27",
        "region": "North",
        "category": "Electronics",
        "author_role": "manager",
    },
    {
        "doc_id": "REVIEW-9950",
        "source_type": "review",
        "title": "Great apparel selection",
        "body": (
            "Love the new apparel range, fast shipping in the South. No complaints "
            "at all — arrived early and well packaged."
        ),
        "date": "2026-05-11",
        "region": "South",
        "category": "Apparel",
        "author_role": "customer",
    },
    {
        "doc_id": "REVIEW-9977",
        "source_type": "review",
        "title": "Home goods are fine",
        "body": (
            "Bought a lamp and a rug for the East region. Delivery was on time and "
            "the quality was acceptable for the price."
        ),
        "date": "2026-06-02",
        "region": "East",
        "category": "Home",
        "author_role": "customer",
    },
    {
        "doc_id": "REPORT-Q2-OPS",
        "source_type": "report",
        "title": "Q2 Operations Review",
        "body": (
            "# Q2 Operations Review\n\n"
            "Overall performance held steady except for one concentrated failure.\n\n"
            "## Fulfilment\n\n"
            "### Root cause\n\n"
            "The North fulfilment centre backlog was the dominant operational issue "
            "of the quarter, concentrated in electronics. A staffing shortfall in "
            "April compounded through May, delaying electronics shipments and driving "
            "cancellations and refunds.\n\n"
            "### Remediation\n\n"
            "Temporary capacity was added and orders were rerouted through the West "
            "centre. Backlog cleared by late June.\n\n"
            "## Other regions\n\n"
            "South, East, and West operated normally with no material service issues."
        ),
        "date": "2026-06-30",
        "region": "North",
        "category": "Electronics",
        "author_role": "manager",
    },
]


def get_sample_documents() -> list[dict]:
    return list(SAMPLE_DOCUMENTS)
