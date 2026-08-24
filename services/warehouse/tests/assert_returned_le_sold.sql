-- Singular test: within any product, returned units can never exceed sold
-- units. A returned line's quantity is by definition part of the sold quantity,
-- so this must hold for every product. Empty result passes.
with per_product as (
    select
        product_key,
        sum(quantity)                                          as units_sold,
        sum(case when is_returned then quantity else 0 end)    as units_returned
    from {{ ref('fact_order_items') }}
    group by product_key
)

select
    product_key,
    units_sold,
    units_returned
from per_product
where units_returned > units_sold
