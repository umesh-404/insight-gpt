-- Conformed store dimension. Region here is the store-side region slice; the
-- customer-side region lives on dim_customer. store_id is the surrogate
-- store_key.
select
    store_id          as store_key,
    store_id,
    store_name,
    region,
    channel,
    opened_date
from {{ ref('stg_stores') }}
