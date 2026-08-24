-- One-to-one with raw.products: dedupe on product_id (keep newest load), cast
-- money to numeric, normalize the active flag. DISTINCT ON keeps join estimates
-- honest for the marts that reference this view.
with source as (
    select * from {{ source('raw', 'products') }}
),

deduped as (
    select distinct on (cast(product_id as integer)) *
    from source
    order by cast(product_id as integer), _loaded_at desc
)

select
    cast(product_id as integer)                   as product_id,
    nullif(trim(sku), '')                         as sku,
    nullif(trim(product_name), '')                as product_name,
    nullif(trim(category), '')                    as category,
    nullif(trim(subcategory), '')                 as subcategory,
    nullif(trim(brand), '')                       as brand,
    cast(nullif(trim(unit_cost), '') as numeric)  as unit_cost,
    cast(nullif(trim(list_price), '') as numeric) as list_price,
    lower(coalesce(nullif(trim(active), ''), 'true')) in ('true', 't', '1', 'yes')
        as active
from deduped
