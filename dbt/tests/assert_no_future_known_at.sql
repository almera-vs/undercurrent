-- Point-in-time guard: nothing in silver may be "known" after the as-of cut-off.
select report_id from {{ ref('reports') }}
where known_at > cast('{{ var("as_of") }}' as timestamptz)
