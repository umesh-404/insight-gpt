-- MetricFlow time spine: one row per calendar day, required by the semantic
-- layer to resolve time-based metric queries. Built from the conformed calendar
-- so it always spans exactly the dataset's date range.
select
    full_date as date_day
from {{ ref('dim_date') }}
