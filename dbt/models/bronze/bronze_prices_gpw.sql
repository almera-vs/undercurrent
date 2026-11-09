select * from {{ lake('bronze', 'prices_gpw') }}
where {{ known_by() }}
