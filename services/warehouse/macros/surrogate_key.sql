{#
  Deterministic surrogate key over one or more fields (dbt_utils-style, but
  vendored so the project needs no package download). Used where a mart needs a
  stable key for a composite grain — e.g. the inventory snapshot
  (snapshot_date x product x store). Conformed dimensions instead reuse their
  stable integer business key as the surrogate, which keeps the *_key columns
  integer-typed as declared in config/semantic_layer.yml.
#}
{% macro surrogate_key(fields) %}
    md5(cast(
        {%- for field in fields %}
            coalesce(cast({{ field }} as varchar), '_null_')
            {%- if not loop.last %} || '||' || {% endif -%}
        {% endfor %}
    as varchar))
{% endmacro %}
