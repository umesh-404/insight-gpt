-- One-to-one with raw.customers: dedupe on the business key (keep the most
-- recently loaded row), then cast types and normalize text. PII columns
-- (email/full_name/phone) are already redacted at ingestion and are
-- intentionally NOT carried forward into the marts.
--
-- Dedup uses DISTINCT ON (idiomatic Postgres) rather than a windowed row_number
-- filter: it gives the planner an accurate row estimate so downstream marts
-- hash-join instead of collapsing into a nested loop.
with source as (
    select * from {{ source('raw', 'customers') }}
),

deduped as (
    select distinct on (cast(customer_id as integer)) *
    from source
    order by cast(customer_id as integer), _loaded_at desc
)

select
    cast(customer_id as integer)                as customer_id,
    nullif(trim(region), '')                    as region,
    nullif(trim(country), '')                   as country,
    lower(nullif(trim(segment), ''))            as segment,
    cast(nullif(trim(signup_date), '') as date) as signup_date
from deduped
