# Warehouse (dbt project)

The dbt-postgres project that turns the landed `raw.*` tables (written by
[`services/ingestion`](../ingestion)) into the modeled star schema and the
governed semantic metrics the insight engine reads. Implements
[`docs/02-data-model.md`](../../docs/02-data-model.md).

## Layers

```
raw (landed, untyped)  ->  staging (cleaned + typed views)  ->  marts (star + metrics)
```

- **staging/** — one view per source (`stg_*`): cast types, normalize text,
  dedupe on the business key (via `DISTINCT ON`), compute `gross_revenue`. One
  staging model maps to exactly one source; no joins.
- **marts/** — materialized tables: the conformed dimensions and fact tables
  from `config/semantic_layer.yml` + the docs/02 star schema.
- **metrics/** — `metrics.yml`: the eight governed metrics as dbt semantic
  models + metrics (MetricFlow), mirroring `config/semantic_layer.yml`.

### Models

| Marts | Grain |
|---|---|
| `dim_date`, `dim_customer`, `dim_product`, `dim_store`, `dim_channel` | conformed dimensions |
| `fact_order_items` | order line (SKU × order) — revenue / margin / units |
| `fact_sales` | order header — AOV / order counts / shipping |
| `fact_inventory_snapshot` | SKU × store × day — stock levels |

### Metrics (docs/02 §6)

`revenue`, `gross_margin`, `gross_margin_pct`, `orders`, `units_sold`,
`avg_order_value`, `return_rate`, `units_on_hand` (plus `returned_units` /
`total_units` as building blocks for `return_rate`).

## Configure (env-driven, no secrets committed)

`profiles.yml` reads everything from the environment:

```bash
export POSTGRES_HOST=localhost POSTGRES_PORT=5432
export POSTGRES_DB=insight POSTGRES_USER=insight_app POSTGRES_PASSWORD=...
export POSTGRES_SCHEMA=marts        # target schema for marts (staging -> "staging")
export INSIGHT_REFERENCE_DATE=2026-07-15   # "no future orders" test boundary
```

The `raw` schema is populated first by the ingestion service (or `scripts/seed.py`).

## Build & test

```bash
dbt deps    # not required — no external packages are used
dbt seed    --project-dir services/warehouse --profiles-dir services/warehouse
dbt run     --project-dir services/warehouse --profiles-dir services/warehouse
dbt test    --project-dir services/warehouse --profiles-dir services/warehouse
# or all at once:
dbt build   --project-dir services/warehouse --profiles-dir services/warehouse
```

Or run the whole pipeline (generate → load raw → run → test) with
[`scripts/seed.py`](../../scripts/seed.py).

## Data-quality tests (docs/02 §7)

- generic: `not_null` / `unique` on every surrogate key, `relationships` on
  every FK to its dim, `accepted_values` on status / channel / category /
  segment;
- singular (`tests/`): `assert_no_future_orders`, `assert_non_negative_revenue`,
  `assert_returned_le_sold`, `assert_unique_inventory_grain`;
- source **freshness** declared on `raw.*` (keys off the `_loaded_at` ingestion
  metadata column).

## Notes

- Requires **dbt ≥ 1.6** (the semantic layer). Parsing the metrics needs no
  extra install; *querying* them needs `dbt-metricflow`. Verified with
  dbt-postgres 1.12 (`dbt parse`, `dbt run`, `dbt build`, `dbt test` all green).
- The `accepted_values` tests use the classic top-level argument form for
  compatibility back to dbt 1.7; dbt ≥ 1.10 emits a deprecation notice for this
  (harmless — the tests still run and pass).
- Schemas land as exactly `staging` and `marts` (a `generate_schema_name`
  override skips dbt's default `<target>_<custom>` prefixing) so the marts sit
  where `config/semantic_layer.yml`'s allow-list expects them.
- `*_key` surrogates reuse the stable integer business keys (keeping the columns
  integer-typed as the semantic layer declares); the `surrogate_key` macro is
  used for the composite inventory-snapshot key.
