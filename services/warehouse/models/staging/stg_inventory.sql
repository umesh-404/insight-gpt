-- One-to-one with raw.inventory: dedupe on the snapshot grain
-- (snapshot_date x product x store), then cast the counts.
with source as (
    select * from {{ source('raw', 'inventory') }}
),

deduped as (
    select distinct on (
        cast(nullif(trim(snapshot_date), '') as date),
        cast(product_id as integer),
        cast(store_id as integer)
    ) *
    from source
    order by
        cast(nullif(trim(snapshot_date), '') as date),
        cast(product_id as integer),
        cast(store_id as integer),
        _loaded_at desc
)

select
    cast(nullif(trim(snapshot_date), '') as date) as snapshot_date,
    cast(product_id as integer)                   as product_id,
    cast(store_id as integer)                     as store_id,
    cast(units_on_hand as integer)                as units_on_hand,
    cast(units_reserved as integer)               as units_reserved,
    cast(reorder_point as integer)                as reorder_point,
    cast(lead_time_days as integer)               as lead_time_days
from deduped
