select * from {{ lake('bronze', 'prices_nc') }}
where {{ known_by() }}
