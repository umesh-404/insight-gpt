-- Singular test: the inventory snapshot grain (snapshot_date x product x store)
-- is unique. Replaces the dbt_utils composite-uniqueness test without needing an
-- external package. Empty result passes.
select
    snapshot_date,
    product_key,
    store_key,
    count(*) as n
from {{ ref('fact_inventory_snapshot') }}
group by snapshot_date, product_key, store_key
having count(*) > 1
