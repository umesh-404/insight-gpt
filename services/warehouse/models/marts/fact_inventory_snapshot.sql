-- Periodic snapshot fact: one row per SKU x store x day. Additive across
-- stores/SKUs but NOT across dates (never sum stock over time — pick a date).
-- A surrogate inv_snapshot_key is hashed over the composite grain via the
-- vendored surrogate_key macro.
with inv as (
    select * from {{ ref('stg_inventory') }}
)

select
    {{ surrogate_key(['snapshot_date', 'product_id', 'store_id']) }} as inv_snapshot_key,
    cast(to_char(snapshot_date, 'YYYYMMDD') as integer) as date_key,
    snapshot_date,
    product_id        as product_key,
    store_id          as store_key,
    units_on_hand,
    units_reserved,
    reorder_point,
    lead_time_days
from inv
