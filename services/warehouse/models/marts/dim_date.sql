-- Conformed calendar spanning every date that appears in the facts (orders +
-- inventory snapshots). Exposes the labels config/semantic_layer.yml slices on
-- (week_label, month_label, quarter_label like '2026Q2', cal_year) plus the
-- day/weekend flags docs/02 lists. Generated in-warehouse from the data bounds.
with days as (
    select order_ts::date as d from {{ ref('stg_orders') }}
    union
    select snapshot_date as d from {{ ref('stg_inventory') }}
),

bounds as (
    select min(d) as start_date, max(d) as end_date from days
),

spine as (
    select generate_series(b.start_date, b.end_date, interval '1 day')::date as full_date
    from bounds b
)

select
    cast(to_char(full_date, 'YYYYMMDD') as integer)     as date_key,
    full_date,
    cast(extract(year from full_date) as integer)       as cal_year,
    cast(extract(quarter from full_date) as integer)    as quarter,
    cast(extract(month from full_date) as integer)      as month,
    cast(extract(week from full_date) as integer)       as week,
    to_char(full_date, 'YYYY-MM')                       as month_label,
    to_char(full_date, 'IYYY-"W"IW')                    as week_label,
    cast(extract(year from full_date) as integer)
        || 'Q' || cast(extract(quarter from full_date) as integer) as quarter_label,
    trim(to_char(full_date, 'Day'))                     as day_of_week,
    extract(isodow from full_date) in (6, 7)            as is_weekend
from spine
