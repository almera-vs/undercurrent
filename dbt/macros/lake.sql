{# Read every Parquet file of a lake table: daily partitions and compacted months. #}
{% macro lake(layer, table) -%}
read_parquet('{{ var("data_dir") }}/{{ layer }}/{{ table }}/*/*.parquet', union_by_name = true)
{%- endmacro %}

{# Rebuilt-every-run Python outputs live in the work dir, not on the data branch. #}
{% macro work_file(path) -%}
read_parquet('{{ var("work_dir") }}/{{ path }}')
{%- endmacro %}

{# Point-in-time filter: hide rows that were not yet known at var('as_of'). #}
{% macro known_by(col='known_at') -%}
{{ col }} <= cast('{{ var("as_of") }}' as timestamptz)
{%- endmacro %}
