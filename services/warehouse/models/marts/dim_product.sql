-- Conformed product dimension. Carries unit_cost (feeds gross margin) and the
-- category/subcategory the semantic layer slices by. product_id is the stable
-- integer surrogate product_key.
select
    product_id        as product_key,
    product_id,
    sku,
    product_name,
    category,
    subcategory,
    brand,
    unit_cost,
    list_price,
    active
from {{ ref('stg_products') }}
