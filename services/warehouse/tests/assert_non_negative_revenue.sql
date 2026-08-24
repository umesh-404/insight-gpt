-- Singular test: net line revenue (gross minus discount) is never negative — a
-- discount must never exceed the gross it applies to. Empty result passes.
select
    order_item_key,
    gross_revenue,
    discount_amount,
    (gross_revenue - discount_amount) as net_revenue
from {{ ref('fact_order_items') }}
where (gross_revenue - discount_amount) < 0
