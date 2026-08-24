"""Generator configuration — one seed, one grid, one planted-dip definition.

All tunables live here so the shape of the dataset is inspectable and the
planted story (``docs/02-data-model.md`` §8) is declared in one place rather than
scattered through the generation code.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

# --- controlled vocabularies (mirror the fixture + docs/02 §2) -----------------
REGIONS: tuple[str, ...] = ("North", "South", "East", "West")
CATEGORIES: tuple[str, ...] = ("Electronics", "Apparel", "Home")
CHANNELS: tuple[str, ...] = ("online", "retail", "marketplace", "wholesale")
SEGMENTS: tuple[str, ...] = ("consumer", "SMB", "enterprise")

# Subcategories per category (drives dim_product.subcategory).
SUBCATEGORIES: dict[str, tuple[str, ...]] = {
    "Electronics": ("Laptops", "Phones", "Audio", "Accessories"),
    "Apparel": ("Tops", "Outerwear", "Footwear"),
    "Home": ("Kitchen", "Furniture", "Decor"),
}

# Expected order *lines* per day for each (region, category) BEFORE seasonality
# and the planted dip. Proportional to the fixture's Q1 revenue grid so the
# generated warehouse tells the same story at a realistic volume.
BASE_DAILY_LINES: dict[tuple[str, str], float] = {
    ("North", "Electronics"): 20.0, ("North", "Apparel"): 12.0, ("North", "Home"): 8.0,
    ("South", "Electronics"): 15.0, ("South", "Apparel"): 10.0, ("South", "Home"): 7.0,
    ("East", "Electronics"): 13.0, ("East", "Apparel"): 9.0, ("East", "Home"): 6.0,
    ("West", "Electronics"): 14.0, ("West", "Apparel"): 9.5, ("West", "Home"): 6.5,
}


class GeneratorConfig(BaseModel):
    """Every knob the generator reads. Immutable per run."""

    seed: int = 42

    # Date span. Default: one year ending Q2 2026, so the holiday season (late
    # 2025) gives visible seasonality and Q2 2026 carries the planted dip. With
    # the reference "today" of 2026-07-15, "last quarter" resolves to 2026 Q2.
    start_date: dt.date = dt.date(2025, 7, 1)
    end_date: dt.date = dt.date(2026, 6, 30)

    # Entity counts.
    n_customers: int = 300
    products_per_subcategory: int = 3
    stores_per_region: int = 2

    # Global volume multiplier applied to BASE_DAILY_LINES (keeps CSVs light and
    # tests fast without changing the story).
    volume_scale: float = 0.4

    # --- the planted dip (docs/02 §8) -----------------------------------------
    # The quarter and the region x category that collapse, plus the multipliers
    # that reproduce the fixture's shape (North+Electronics ~0.45 of normal).
    dip_year: int = 2026
    dip_quarter: int = 2
    dip_region: str = "North"
    dip_category: str = "Electronics"
    dip_factor: float = 0.45          # North x Electronics in the dip quarter
    dip_region_factor: float = 0.90   # North, other categories, same quarter
    dip_other_factor: float = 0.98    # everyone else, same quarter (mild softening)

    # Returns: baseline, and the elevated rate for flagged high-return SKUs and
    # for the dipped segment (cancellations/refunds from the backlog).
    base_return_rate: float = 0.04
    high_return_rate: float = 0.18
    dip_return_rate: float = 0.22

    # Fraction of lines carrying an order/line discount, and its size band.
    discount_line_fraction: float = 0.35

    currency: str = "USD"

    # Optional parquet output in addition to CSV (requires pyarrow; skipped with
    # a message if unavailable).
    write_parquet: bool = False

    model_config = {"frozen": True}

    def dip_multiplier(self, region: str, category: str, when: dt.date) -> float:
        """Demand multiplier for the planted dip at ``when`` (1.0 outside it)."""
        q = (when.month - 1) // 3 + 1
        if when.year != self.dip_year or q != self.dip_quarter:
            return 1.0
        if region == self.dip_region and category == self.dip_category:
            return self.dip_factor
        if region == self.dip_region:
            return self.dip_region_factor
        return self.dip_other_factor

    def in_dip(self, region: str, category: str, when: dt.date) -> bool:
        q = (when.month - 1) // 3 + 1
        return (
            when.year == self.dip_year
            and q == self.dip_quarter
            and region == self.dip_region
            and category == self.dip_category
        )


def quarter_label(when: dt.date) -> str:
    """'2026Q2'-style label, matching config/semantic_layer.yml dim_date."""
    q = (when.month - 1) // 3 + 1
    return f"{when.year}Q{q}"


def daterange(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive list of dates from ``start`` to ``end``."""
    days = (end - start).days
    return [start + dt.timedelta(days=i) for i in range(days + 1)]
