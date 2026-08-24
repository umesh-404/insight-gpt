-- Conformed channel dimension, derived from the distinct channels present on
-- stores. A dense integer channel_key is assigned deterministically by channel
-- name so fact rows can join on it. Exposes channel_name per
-- config/semantic_layer.yml.
with channels as (
    select distinct channel as channel_name
    from {{ ref('stg_stores') }}
    where channel is not null
),

keyed as (
    select
        cast(row_number() over (order by channel_name) as integer) as channel_key,
        channel_name
    from channels
)

select
    k.channel_key,
    k.channel_name,
    m.channel_group,
    m.is_digital
from keyed k
left join {{ ref('channel_map') }} m on k.channel_name = m.channel_name
