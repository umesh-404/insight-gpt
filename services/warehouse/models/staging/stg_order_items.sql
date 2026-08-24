-- One-to-one with raw.order_items: dedupe on order_item_id, cast quantity/price,
-- coalesce the line discount to 0, compute gross_revenue = quantity * unit_price,
-- and normalize the returned flag. Cost is attached later in the fact (it needs
-- dim_product).
with source as (
    select * from {{ source('raw', 'order_items') }}
),

deduped as (
    select distinct on (cast(order_item_id as integer)) *
    from source
    order by cast(order_item_id as integer), _loaded_at desc
),

typed as (
    select
        cast(order_item_id as integer)                as order_item_id,
        cast(order_id as integer)                     as order_id,
        cast(product_id as integer)                   as product_id,
        cast(quantity as integer)                     as quantity,
        cast(nullif(trim(unit_price), '') as numeric) as unit_price,
        coalesce(cast(nullif(trim(discount_amount), '') as numeric), 0) as discount_amount,
        lower(coalesce(nullif(trim(is_returned), ''), 'false')) in ('true', 't', '1', 'yes')
            as is_returned,
        nullif(trim(return_reason), '')               as return_reason
    from deduped
)

select
    order_item_id,
    order_id,
    product_id,
    quantity,
    unit_price,
    discount_amount,
    (quantity * unit_price) as gross_revenue,
    is_returned,
    return_reason
from typed
