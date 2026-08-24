-- Order-header fact: one row per order, so order-level metrics (AOV, order
-- counts, shipping) are not distorted by fanning out to line grain (docs/02
-- §4.1). order_revenue is the net of line gross minus line discounts; header
-- discount and shipping come from the order.
with orders as (
    select * from {{ ref('stg_orders') }}
),

stores as (
    select * from {{ ref('stg_stores') }}
),

channels as (
    select * from {{ ref('dim_channel') }}
),

line_rollup as (
    select
        order_id,
        sum(gross_revenue - discount_amount) as net_line_revenue,
        count(*)                             as line_count
    from {{ ref('stg_order_items') }}
    group by order_id
)

select
    o.order_id                              as order_key,
    cast(to_char(o.order_ts, 'YYYYMMDD') as integer) as date_key,
    o.order_ts::date                        as order_date,
    o.customer_id                           as customer_key,
    o.store_id                              as store_key,
    ch.channel_key                          as channel_key,
    coalesce(lr.net_line_revenue, 0)        as order_revenue,
    o.discount_amount                       as discount_amount,
    o.shipping_amount                       as shipping_amount,
    coalesce(lr.line_count, 0)              as line_count,
    o.status                                as status
from orders o
inner join stores s     on o.store_id = s.store_id
inner join channels ch  on s.channel = ch.channel_name
left join line_rollup lr on o.order_id = lr.order_id
