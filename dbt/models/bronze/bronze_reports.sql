select * from {{ lake('bronze', 'reports') }}
where {{ known_by() }}
