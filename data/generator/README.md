# Synthetic data generator

A **deterministic, seedable** retail / e-commerce data generator. It produces the
operational source shapes from [`docs/02-data-model.md`](../../docs/02-data-model.md)
§2 plus the document sources, with weekly + holiday seasonality and an
intentional single-quarter dip so the headline questions have real answers.

Same seed in, byte-identical data out — no wall-clock reads, no unseeded
randomness.

## What it produces

Written to `data/generated/` (git-ignored, regenerable):

| File | Grain | Notes |
|---|---|---|
| `customers.csv` | one per customer | includes synthetic PII (redacted at ingestion) |
| `products.csv` | one per SKU | category / subcategory / brand, cost + list price |
| `stores.csv` | one per store | carries the channel (all four channels appear) |
| `orders.csv` | one per order | header: status, currency, discount, shipping |
| `order_items.csv` | one per SKU × order | quantity, unit_price, discount, returns |
| `inventory.csv` | one per SKU × store × snapshot day | weekly cadence by default |
| `documents/support_tickets.json` | one per ticket | free text + filter metadata |
| `documents/reviews.json` | one per review | rating 1–5 + free text |
| `documents/reports.json` | one per quarter | quarterly business report |

## The planted story (`docs/02` §8)

In **2026 Q2**, revenue for **North × Electronics** collapses to ~45% of Q1
(fulfilment backlog), while other regions/categories only soften mildly. The
same quarter carries:

- a **modeled stockout** — North Electronics inventory runs near zero with
  elevated lead times (drives the "which products should we restock?" answer);
- a **document echo** — a spike of negative North Electronics reviews and
  fulfilment-backlog support tickets, plus a Q2 business report naming the cause;
- **correlated returns** — flagged high-return SKUs and elevated returns in the
  dipped segment.

This reproduces the shape of the engine fixture
(`services/api/app/fixtures/retail.py`) at a realistic volume, so
*"why did sales decline last quarter?"* is answerable by decomposition **and**
cross-referenced against document themes.

## Run it

```bash
# default: seed 42, one year ending 2026-06-30, ~20k order lines
python -m data.generator

# options
python -m data.generator --seed 7 --scale 0.6 --parquet
python -m data.generator --start 2025-01-01 --end 2025-12-31 --customers 500
python -m data.generator --out /tmp/mydata
```

`--parquet` additionally writes parquet if `pyarrow` is installed (skipped with a
message otherwise). CSV always uses only the standard library.

Programmatic use:

```python
from data.generator import GeneratorConfig, generate

result = generate(GeneratorConfig(seed=42))
print(result.counts)  # rows written per file
```

## Tests

Offline, no database required:

```bash
pytest data/generator/tests
```

They assert the planted dip is present and concentrated, referential integrity
across the CSVs, quality invariants (no future orders, non-negative revenue,
returned ≤ sold, stockout in the dip window), that the documents echo the dip,
and determinism (same seed → identical bytes; different seed → different data).

## Configuration

All tunables live in [`config.py`](config.py) — date span, entity counts,
volume, the dip quarter/region/category and its multipliers, return rates, and
discount rate. The controlled vocabularies (regions, categories, channels,
segments) are declared there too.
