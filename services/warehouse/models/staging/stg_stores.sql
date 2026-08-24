-- One-to-one with raw.stores. Dedupe on store_id; normalize channel to the
-- controlled vocabulary used by dim_channel
-- (online / retail / marketplace / wholesale).
with source as (
    select * from {{ source('raw', 'stores') }}
),

deduped as (
    select distinct on (cast(store_id as integer)) *
    from source
    order by cast(store_id as integer), _loaded_at desc
)

select
    cast(store_id as integer)                   as store_id,
    nullif(trim(store_name), '')                as store_name,
    lower(nullif(trim(channel), ''))            as channel,
    nullif(trim(region), '')                    as region,
    cast(nullif(trim(opened_date), '') as date) as opened_date
from deduped
