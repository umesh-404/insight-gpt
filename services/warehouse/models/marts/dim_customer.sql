-- Conformed customer dimension. The stable integer business key doubles as the
-- surrogate customer_key (keeps the *_key column integer-typed as declared in
-- config/semantic_layer.yml). Carries region and segment — the dimensions the
-- semantic layer slices revenue/orders by.
select
    customer_id       as customer_key,
    customer_id,
    region,
    country,
    segment,
    signup_date
from {{ ref('stg_customers') }}
