-- One-to-one with raw.orders: dedupe on order_id (keep newest load), cast the
-- timestamp, coalesce money to 0, and normalize status to the controlled set
-- (placed / shipped / delivered / cancelled / returned).
with source as (
    select * from {{ source('raw', 'orders') }}
),

deduped as (
    select distinct on (cast(order_id as integer)) *
    from source
    order by cast(order_id as integer), _loaded_at desc
)

select
    cast(order_id as integer)                          as order_id,
    cast(customer_id as integer)                       as customer_id,
    cast(store_id as integer)                          as store_id,
    cast(nullif(trim(order_ts), '') as timestamp)      as order_ts,
    lower(nullif(trim(status), ''))                    as status,
    upper(coalesce(nullif(trim(currency), ''), 'USD')) as currency,
    coalesce(cast(nullif(trim(discount_amount), '') as numeric), 0)  as discount_amount,
    coalesce(cast(nullif(trim(shipping_amount), '') as numeric), 0)  as shipping_amount
from deduped
