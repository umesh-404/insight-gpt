-- The workhorse fact: one row per order line (SKU x order). Joins staged lines
-- to the conformed dimension keys, attaches date_key from the order timestamp,
-- and computes cost_amount = quantity * unit_cost (needs dim_product). Measures
-- match config/semantic_layer.yml: gross_revenue, discount_amount, cost_amount,
-- quantity, is_returned. order_date is kept as the metrics time dimension.
with lines as (
    select * from {{ ref('stg_order_items') }}
),

orders as (
    select * from {{ ref('stg_orders') }}
),

products as (
    select * from {{ ref('stg_products') }}
),

stores as (
    select * from {{ ref('stg_stores') }}
),

channels as (
    select * from {{ ref('dim_channel') }}
)

select
    li.order_item_id                        as order_item_key,
    o.order_id                              as order_key,
    cast(to_char(o.order_ts, 'YYYYMMDD') as integer) as date_key,
    o.order_ts::date                        as order_date,
    li.product_id                           as product_key,
    o.customer_id                           as customer_key,
    o.store_id                              as store_key,
    ch.channel_key                          as channel_key,
    li.quantity                             as quantity,
    li.gross_revenue                        as gross_revenue,
    li.discount_amount                      as discount_amount,
    (li.quantity * p.unit_cost)             as cost_amount,
    li.is_returned                          as is_returned,
    li.return_reason                        as return_reason
from lines li
inner join orders o     on li.order_id = o.order_id
inner join products p   on li.product_id = p.product_id
inner join stores s     on o.store_id = s.store_id
inner join channels ch  on s.channel = ch.channel_name
