select * from {{ lake('bronze', 'indices_gpw') }}
where {{ known_by() }}
