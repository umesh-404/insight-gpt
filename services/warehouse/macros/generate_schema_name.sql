{#
  Use the model's configured schema verbatim (staging / marts) instead of dbt's
  default "<target_schema>_<custom>" concatenation, so the warehouse has exactly
  the three schemas docs/02 describes (raw / staging / marts) and the marts land
  where config/semantic_layer.yml's allow-list expects them.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
