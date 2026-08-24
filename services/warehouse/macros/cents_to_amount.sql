{#
  Convert an integer-cents column to a numeric currency amount. Provided per the
  project structure in docs/02 §5. The synthetic feeds already export decimal
  amounts, so staging casts directly; this macro is here for feeds that land
  money as integer cents.
#}
{% macro cents_to_amount(column) %}
    (cast({{ column }} as numeric) / 100.0)
{% endmacro %}
