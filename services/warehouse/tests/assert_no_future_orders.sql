-- Singular test: no order may be dated after the reference "as of" date. Guards
-- against a feed with clock-skewed or bad timestamps. Returns offending rows;
-- an empty result passes. Reference date is a project var (default 2026-07-15).
select
    order_key,
    order_date
from {{ ref('fact_sales') }}
where order_date > cast('{{ var("reference_date") }}' as date)
